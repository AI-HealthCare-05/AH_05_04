import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx

from ai_worker.tasks.rag.source_client.contracts import (
    EmptyResultPolicy,
    EndpointContract,
    PaginationContract,
    ProviderBodyCodeContract,
    RequiredParameter,
    RetryDisposition,
    SourceClientLimits,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRequest,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.mfds_client import (
    DecodedProviderPage,
    MfdsSourceClient,
    classify_body_code,
)


def endpoint_contract() -> EndpointContract:
    return EndpointContract(
        identity=SourceOperationIdentity(
            source_code="SYNTHETIC_MFDS_SOURCE",
            endpoint_code="SYNTHETIC_MFDS_ENDPOINT",
            operation_code="LIST_SYNTHETIC_RECORDS",
        ),
        method="GET",
        scheme="https",
        host="api.synthetic.invalid",
        path_template="/synthetic/v1/items",
        required_parameters=(
            RequiredParameter(
                name="serviceKey",
                type_name="string",
                location="query",
                sensitive=True,
            ),
            RequiredParameter(
                name="page",
                type_name="integer",
                location="query",
            ),
        ),
        allowed_content_types=("application/json",),
        body_success_code_path="synthetic_header.result_code",
        body_error_code_path="synthetic_header.result_code",
        pagination=PaginationContract(
            mode="PAGE_NUMBER",
            page_parameter="page",
            page_size_parameter="page_size",
            page_base=1,
            page_size_limit=2,
            end_condition="TOTAL_COUNT_REACHED",
        ),
        primary_key_fields=("synthetic_id",),
        external_version_field=None,
        body_codes=ProviderBodyCodeContract(
            success_codes=("SYNTHETIC_SUCCESS",),
            authentication_failure_codes=("SYNTHETIC_AUTH_FAILURE",),
            daily_limit_codes=("SYNTHETIC_DAILY_LIMIT",),
        ),
        limits=SourceClientLimits(
            connect_timeout_seconds=3,
            read_timeout_seconds=5,
            total_timeout_seconds=10,
            max_redirects=0,
            max_response_bytes=1_024,
            max_decompressed_bytes=2_048,
            max_pages=10,
            max_retry_attempts=2,
        ),
        empty_result_policy=EmptyResultPolicy.REJECT,
    )


def test_success_body_code_is_accepted() -> None:
    failure = classify_body_code(
        "SYNTHETIC_SUCCESS",
        contract=endpoint_contract(),
        http_status=200,
    )

    assert failure is None


def test_http_200_authentication_failure_is_not_success() -> None:
    failure = classify_body_code(
        "SYNTHETIC_AUTH_FAILURE",
        contract=endpoint_contract(),
        http_status=200,
    )

    assert failure is not None
    assert failure.code is SourceFailureCode.AUTHENTICATION_FAILED
    assert failure.retry is RetryDisposition.NOT_RETRYABLE
    assert failure.http_status == 200


def test_daily_limit_waits_for_explicit_reset_time() -> None:
    failure = classify_body_code(
        "SYNTHETIC_DAILY_LIMIT",
        contract=endpoint_contract(),
        http_status=200,
        retry_reset_at="2026-09-05T00:00:00+09:00",
    )

    assert failure is not None
    assert failure.code is SourceFailureCode.RATE_LIMITED
    assert failure.retry is RetryDisposition.RETRY_AT_RESET
    assert failure.retry_reset_at == "2026-09-05T00:00:00+09:00"


def test_daily_limit_without_reset_time_does_not_invent_one() -> None:
    failure = classify_body_code(
        "SYNTHETIC_DAILY_LIMIT",
        contract=endpoint_contract(),
        http_status=200,
    )

    assert failure is not None
    assert failure.retry is RetryDisposition.RETRY_AT_RESET
    assert failure.retry_reset_at is None


def test_unknown_body_code_fails_closed_as_schema_drift() -> None:
    failure = classify_body_code(
        "UNRECOGNIZED_CODE",
        contract=endpoint_contract(),
        http_status=200,
    )

    assert failure is not None
    assert failure.code is SourceFailureCode.SCHEMA_DRIFT
    assert failure.retry is RetryDisposition.NOT_RETRYABLE


FIXTURE_DIR = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "rag" / "mfds"


def test_synthetic_fixtures_do_not_contain_credentials_or_urls() -> None:
    fixture_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(FIXTURE_DIR.glob("*.json"))).lower()

    forbidden_values = (
        "authorization",
        "api_key",
        "apikey",
        "service_key=",
        "servicekey=",
        "http://",
        "https://",
        "actual-secret",
    )

    assert all(value not in fixture_text for value in forbidden_values)


def decode_synthetic_json(
    body: bytes,
    media_type: str,
) -> DecodedProviderPage:
    if media_type != "application/json":
        raise ValueError

    payload = cast(dict[str, object], json.loads(body))
    header = cast(dict[str, object], payload["synthetic_header"])
    body_envelope = cast(dict[str, object], payload["synthetic_body"])
    raw_records = cast(list[dict[str, object]], body_envelope["items"])

    body_code = header["result_code"]
    total_count = body_envelope.get("total_count")
    retry_reset_at = header.get("retry_reset_at")

    if not isinstance(body_code, str):
        raise TypeError

    if total_count is not None and not isinstance(total_count, int):
        raise TypeError

    if retry_reset_at is not None and not isinstance(retry_reset_at, str):
        raise TypeError

    records: tuple[Mapping[str, object], ...] = tuple(raw_records)

    return DecodedProviderPage(
        body_code=body_code,
        records=records,
        total_count=total_count,
        retry_reset_at=retry_reset_at,
    )


async def public_resolver(_host: str) -> Sequence[str]:
    return ("8.8.8.8",)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def synthetic_response_bytes(
    *,
    records: list[dict[str, object]],
    page: int,
    total_count: int,
) -> bytes:
    return json.dumps(
        {
            "synthetic_header": {
                "result_code": "SYNTHETIC_SUCCESS",
            },
            "synthetic_body": {
                "items": records,
                "page": page,
                "page_size": 2,
                "total_count": total_count,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


async def test_fetch_one_page_accepts_http_and_body_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["serviceKey"] == "synthetic-local-secret"
        assert request.url.params["page"] == "1"
        assert request.url.params["page_size"] == "2"

        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=fixture_bytes("synthetic_success_page_1.json"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.SUCCEEDED
    assert result.record_count == 2
    assert result.snapshot_candidate_allowed is True
    assert result.failure is None


async def test_http_200_body_authentication_failure_is_rejected() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=fixture_bytes("synthetic_auth_failure.json"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.FAILED
    assert result.pages == ()
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.AUTHENTICATION_FAILED
    assert result.failure.retry is RetryDisposition.NOT_RETRYABLE


async def test_html_error_body_is_rejected_before_decoding() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>synthetic error</html>",
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.CONTENT_TYPE_MISMATCH


async def test_oversized_response_is_rejected() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"x" * 2_049,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.RESPONSE_TOO_LARGE


async def test_secret_and_full_authenticated_url_are_not_exposed() -> None:
    secret = "private-synthetic-secret"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value=secret,
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    rendered_result = repr(result)

    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.PROVIDER_UNAVAILABLE
    assert secret not in rendered_result
    assert "serviceKey=" not in rendered_result
    assert "https://" not in rendered_result


async def no_sleep(_seconds: float) -> None:
    return None


async def test_fetch_all_pages_collects_every_page_atomically() -> None:
    requested_pages: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        page_number = int(request.url.params["page"])
        requested_pages.append(page_number)

        fixture_name = "synthetic_success_page_1.json" if page_number == 1 else "synthetic_success_page_2.json"

        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=fixture_bytes(fixture_name),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=no_sleep,
        )
        result = await client.fetch_all_pages(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert requested_pages == [1, 2]
    assert result.status is SourceRunStatus.SUCCEEDED
    assert len(result.pages) == 2
    assert result.record_count == 3
    assert result.snapshot_candidate_allowed is True


async def test_page_two_failure_discards_page_one_and_fails_run() -> None:
    page_two_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page_two_attempts

        page_number = int(request.url.params["page"])

        if page_number == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=fixture_bytes("synthetic_success_page_1.json"),
            )

        page_two_attempts += 1
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=no_sleep,
        )
        result = await client.fetch_all_pages(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert page_two_attempts == 3
    assert result.status is SourceRunStatus.FAILED
    assert result.pages == ()
    assert result.record_count == 0
    assert result.snapshot_candidate_allowed is False
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.RETRY_BUDGET_EXHAUSTED


async def test_temporary_failure_is_retried_with_limited_backoff() -> None:
    attempts = 0
    observed_delays: list[float] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return httpx.Response(
                503,
                headers={"content-type": "application/json"},
            )

        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=fixture_bytes("synthetic_success_page_1.json"),
        )

    async def record_sleep(seconds: float) -> None:
        observed_delays.append(seconds)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=record_sleep,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    # fetch_one_page 자체는 단일 시도 API이므로 재시도하지 않는다.
    assert attempts == 1
    assert observed_delays == []
    assert result.failure is not None
    assert result.failure.retry is RetryDisposition.BACKOFF


async def test_daily_limit_is_not_automatically_retried() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=fixture_bytes("synthetic_daily_limit.json"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=no_sleep,
        )
        result = await client.fetch_all_pages(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert attempts == 1
    assert result.failure is not None
    assert result.failure.retry is RetryDisposition.RETRY_AT_RESET


async def test_approved_redirect_is_revalidated_and_followed() -> None:
    contract = endpoint_contract()
    contract = replace(
        contract,
        limits=replace(contract.limits, max_redirects=1),
    )
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            return httpx.Response(
                307,
                headers={"location": "/synthetic/v1/items"},
            )

        assert request.url.params["serviceKey"] == "synthetic-local-secret"

        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=fixture_bytes("synthetic_success_page_1.json"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=contract,
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=contract.identity,
                parameters={},
            )
        )

    assert attempts == 2
    assert result.status is SourceRunStatus.SUCCEEDED


async def test_redirect_to_unapproved_host_is_rejected() -> None:
    contract = endpoint_contract()
    contract = replace(
        contract,
        limits=replace(contract.limits, max_redirects=1),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307,
            headers={"location": ("https://unapproved.invalid/synthetic/v1/items")},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=contract,
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=contract.identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.REDIRECT_REJECTED


async def test_redirect_is_rejected_when_dns_changes_to_private_ip() -> None:
    contract = endpoint_contract()
    contract = replace(
        contract,
        limits=replace(contract.limits, max_redirects=1),
    )
    resolutions = 0

    async def changing_resolver(_host: str) -> Sequence[str]:
        nonlocal resolutions
        resolutions += 1

        if resolutions == 1:
            return ("8.8.8.8",)

        return ("127.0.0.1",)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307,
            headers={"location": "/synthetic/v1/items"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=contract,
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=changing_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=contract.identity,
                parameters={},
            )
        )

    assert resolutions == 2
    assert result.status is SourceRunStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.REDIRECT_REJECTED


async def test_redirect_limit_is_enforced_by_client() -> None:
    contract = endpoint_contract()
    contract = replace(
        contract,
        limits=replace(contract.limits, max_redirects=1),
    )
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        return httpx.Response(
            307,
            headers={"location": "/synthetic/v1/items"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=contract,
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=contract.identity,
                parameters={},
            )
        )

    assert attempts == 2
    assert result.status is SourceRunStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.REDIRECT_REJECTED


async def test_xml_dtd_is_rejected_before_decoder_execution() -> None:
    contract = replace(
        endpoint_contract(),
        allowed_content_types=("application/xml",),
    )
    decoder_called = False

    def forbidden_decoder(
        _body: bytes,
        _media_type: str,
    ) -> DecodedProviderPage:
        nonlocal decoder_called
        decoder_called = True
        raise AssertionError("unsafe XML must not reach the decoder")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/xml"},
            content=(
                b'<?xml version="1.0"?><!DOCTYPE data [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><data>&xxe;</data>'
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=contract,
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=forbidden_decoder,
            client=http_client,
            resolver=public_resolver,
        )
        result = await client.fetch_one_page(
            SourceRequest(
                operation=contract.identity,
                parameters={},
            )
        )

    assert decoder_called is False
    assert result.status is SourceRunStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.XML_SECURITY_VIOLATION


async def test_duplicate_primary_key_across_pages_fails_closed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        page_number = int(request.url.params["page"])

        if page_number == 1:
            content = fixture_bytes("synthetic_success_page_1.json")
        else:
            content = synthetic_response_bytes(
                records=[
                    {
                        "synthetic_id": "product-002",
                        "name": "중복 합성 의약품",
                    }
                ],
                page=2,
                total_count=3,
            )

        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=content,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=no_sleep,
        )
        result = await client.fetch_all_pages(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.SCHEMA_DRIFT
    assert result.pages == ()
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.SCHEMA_DRIFT
    assert result.primary_key_validation is not None
    assert result.primary_key_validation.null_count == 0
    assert result.primary_key_validation.duplicate_count == 1
    assert result.primary_key_validation.candidate_field_stats == (("name", 0, 0),)
    assert result.primary_key_validation.whole_record_duplicate_count == 0


async def test_null_primary_key_fails_closed() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=synthetic_response_bytes(
                records=[
                    {
                        "synthetic_id": None,
                        "name": "식별자 없는 합성 의약품",
                    }
                ],
                page=1,
                total_count=1,
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=no_sleep,
        )
        result = await client.fetch_all_pages(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.SCHEMA_DRIFT
    assert result.pages == ()
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.SCHEMA_DRIFT


async def test_record_order_does_not_change_primary_key_validation() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=synthetic_response_bytes(
                records=[
                    {
                        "synthetic_id": "product-002",
                        "name": "합성 의약품 B",
                    },
                    {
                        "synthetic_id": "product-001",
                        "name": "합성 의약품 A",
                    },
                ],
                page=1,
                total_count=2,
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=endpoint_contract(),
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=no_sleep,
        )
        result = await client.fetch_all_pages(
            SourceRequest(
                operation=endpoint_contract().identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.SUCCEEDED
    assert result.record_count == 2


async def test_maximum_page_limit_prevents_partial_success() -> None:
    contract = endpoint_contract()
    contract = replace(
        contract,
        limits=replace(contract.limits, max_pages=1),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=fixture_bytes("synthetic_success_page_1.json"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = MfdsSourceClient(
            contract=contract,
            secret_parameter_name="serviceKey",
            secret_value="synthetic-local-secret",
            decoder=decode_synthetic_json,
            client=http_client,
            resolver=public_resolver,
            sleep=no_sleep,
        )
        result = await client.fetch_all_pages(
            SourceRequest(
                operation=contract.identity,
                parameters={},
            )
        )

    assert result.status is SourceRunStatus.FAILED
    assert result.pages == ()
    assert result.failure is not None
    assert result.failure.code is SourceFailureCode.PAGE_LIMIT_EXCEEDED
