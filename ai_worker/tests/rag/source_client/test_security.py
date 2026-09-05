from collections.abc import Sequence

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    EmptyResultPolicy,
    EndpointContract,
    PaginationContract,
    ProviderBodyCodeContract,
    RequiredParameter,
    SourceClientLimits,
    SourceFailureCode,
    SourceOperationIdentity,
)
from ai_worker.tasks.rag.source_client.security import (
    SourceSecurityError,
    reject_unsafe_xml,
    validate_body_sizes,
    validate_operation_url,
    validate_redirect_location,
    validate_response_content_type,
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
                name="page",
                type_name="integer",
                location="query",
            ),
        ),
        allowed_content_types=(
            "application/json",
            "application/xml",
            "text/xml",
        ),
        body_success_code_path="header.resultCode",
        body_error_code_path="header.resultCode",
        pagination=PaginationContract(
            mode="PAGE_NUMBER",
            page_parameter="page",
            page_size_parameter="perPage",
            page_base=1,
            page_size_limit=100,
            end_condition="CURRENT_PAGE_RECORD_COUNT_LT_PAGE_SIZE",
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
            max_redirects=1,
            max_response_bytes=1_024,
            max_decompressed_bytes=2_048,
            max_pages=10,
            max_retry_attempts=2,
        ),
        empty_result_policy=EmptyResultPolicy.REJECT,
    )


async def public_resolver(_host: str) -> Sequence[str]:
    return ("8.8.8.8",)


async def test_approved_https_destination_passes() -> None:
    await validate_operation_url(
        "https://api.synthetic.invalid/synthetic/v1/items",
        contract=endpoint_contract(),
        resolver=public_resolver,
    )


async def test_http_destination_is_rejected() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        await validate_operation_url(
            "http://api.synthetic.invalid/synthetic/v1/items",
            contract=endpoint_contract(),
            resolver=public_resolver,
        )

    assert exc_info.value.code is SourceFailureCode.DESTINATION_REJECTED


async def test_unapproved_host_is_rejected() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        await validate_operation_url(
            "https://unapproved.invalid/synthetic/v1/items",
            contract=endpoint_contract(),
            resolver=public_resolver,
        )

    assert exc_info.value.code is SourceFailureCode.DESTINATION_REJECTED


async def test_unapproved_path_is_rejected() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        await validate_operation_url(
            "https://api.synthetic.invalid/private/admin",
            contract=endpoint_contract(),
            resolver=public_resolver,
        )

    assert exc_info.value.code is SourceFailureCode.DESTINATION_REJECTED


@pytest.mark.parametrize(
    "blocked_address",
    (
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "fe80::1",
    ),
)
async def test_private_or_metadata_address_is_rejected(
    blocked_address: str,
) -> None:
    async def blocked_resolver(_host: str) -> Sequence[str]:
        return (blocked_address,)

    with pytest.raises(SourceSecurityError) as exc_info:
        await validate_operation_url(
            "https://api.synthetic.invalid/synthetic/v1/items",
            contract=endpoint_contract(),
            resolver=blocked_resolver,
        )

    assert exc_info.value.code is SourceFailureCode.DESTINATION_REJECTED


async def test_redirect_to_unapproved_host_is_rejected() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        await validate_redirect_location(
            current_url="https://api.synthetic.invalid/synthetic/v1/items",
            location="https://unapproved.invalid/synthetic/v1/items",
            hop_number=1,
            contract=endpoint_contract(),
            resolver=public_resolver,
        )

    assert exc_info.value.code is SourceFailureCode.REDIRECT_REJECTED


async def test_redirect_to_private_ip_is_rejected() -> None:
    async def private_resolver(_host: str) -> Sequence[str]:
        return ("127.0.0.1",)

    with pytest.raises(SourceSecurityError) as exc_info:
        await validate_redirect_location(
            current_url="https://api.synthetic.invalid/synthetic/v1/items",
            location="/synthetic/v1/items",
            hop_number=1,
            contract=endpoint_contract(),
            resolver=private_resolver,
        )

    assert exc_info.value.code is SourceFailureCode.REDIRECT_REJECTED


async def test_redirect_limit_is_enforced() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        await validate_redirect_location(
            current_url="https://api.synthetic.invalid/synthetic/v1/items",
            location="/synthetic/v1/items",
            hop_number=2,
            contract=endpoint_contract(),
            resolver=public_resolver,
        )

    assert exc_info.value.code is SourceFailureCode.REDIRECT_REJECTED


def test_approved_content_type_is_normalized() -> None:
    media_type = validate_response_content_type(
        "application/json; charset=utf-8",
        contract=endpoint_contract(),
    )

    assert media_type == "application/json"


def test_html_error_body_is_rejected() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        validate_response_content_type(
            "text/html; charset=utf-8",
            contract=endpoint_contract(),
        )

    assert exc_info.value.code is SourceFailureCode.CONTENT_TYPE_MISMATCH


def test_transferred_response_size_is_limited() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        validate_body_sizes(
            raw_size=1_025,
            decompressed_size=1_025,
            limits=endpoint_contract().limits,
        )

    assert exc_info.value.code is SourceFailureCode.RESPONSE_TOO_LARGE


def test_decompressed_response_size_is_limited() -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        validate_body_sizes(
            raw_size=100,
            decompressed_size=2_049,
            limits=endpoint_contract().limits,
        )

    assert exc_info.value.code is SourceFailureCode.RESPONSE_TOO_LARGE


@pytest.mark.parametrize(
    "unsafe_xml",
    (
        b'<?xml version="1.0"?><!DOCTYPE root><root/>',
        b'<?xml version="1.0"?><!ENTITY secret SYSTEM "file:///etc/passwd">',
        b"<!doctype root [<!entity xxe SYSTEM 'https://attacker.invalid'>]>",
    ),
)
def test_xml_dtd_and_external_entity_are_rejected(
    unsafe_xml: bytes,
) -> None:
    with pytest.raises(SourceSecurityError) as exc_info:
        reject_unsafe_xml(unsafe_xml)

    assert exc_info.value.code is SourceFailureCode.XML_SECURITY_VIOLATION


def test_security_error_does_not_expose_url_or_secret() -> None:
    error = SourceSecurityError(
        SourceFailureCode.DESTINATION_REJECTED,
        "Source destination is not approved.",
    )

    rendered = str(error).lower()

    assert "servicekey" not in rendered
    assert "secret" not in rendered
    assert "https://" not in rendered
