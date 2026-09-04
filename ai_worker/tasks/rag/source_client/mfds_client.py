"""Common MFDS source client behavior."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

import httpx

from ai_worker.tasks.rag.source_client.contracts import (
    EndpointContract,
    ProviderPage,
    RetryDisposition,
    SourceClientFailure,
    SourceFailureCode,
    SourceRequest,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.security import (
    HostResolver,
    SourceSecurityError,
    reject_unsafe_xml,
    resolve_host,
    validate_body_sizes,
    validate_operation_url,
    validate_redirect_location,
    validate_response_content_type,
)


@dataclass(frozen=True, slots=True)
class DecodedProviderPage:
    """Minimal decoded page returned by a versioned response decoder."""

    body_code: str
    records: tuple[Mapping[str, object], ...]
    total_count: int | None
    retry_reset_at: str | None = None


type ResponseDecoder = Callable[
    [bytes, str],
    DecodedProviderPage,
]


type Sleep = Callable[[float], Awaitable[None]]


class _PaginationDecision(StrEnum):
    CONTINUE = "CONTINUE"
    COMPLETE = "COMPLETE"
    INVALID = "INVALID"


def classify_body_code(
    body_code: str,
    *,
    contract: EndpointContract,
    http_status: int,
    retry_reset_at: str | None = None,
) -> SourceClientFailure | None:
    """Classify a provider body code without exposing the response body."""

    if body_code in contract.body_codes.success_codes:
        return None

    if body_code in contract.body_codes.authentication_failure_codes:
        return SourceClientFailure(
            code=SourceFailureCode.AUTHENTICATION_FAILED,
            retry=RetryDisposition.NOT_RETRYABLE,
            safe_message="MFDS authentication failed.",
            http_status=http_status,
        )

    if body_code in contract.body_codes.daily_limit_codes:
        return SourceClientFailure(
            code=SourceFailureCode.RATE_LIMITED,
            retry=RetryDisposition.RETRY_AT_RESET,
            safe_message="MFDS daily request limit was reached.",
            http_status=http_status,
            retry_reset_at=retry_reset_at,
        )

    return SourceClientFailure(
        code=SourceFailureCode.SCHEMA_DRIFT,
        retry=RetryDisposition.NOT_RETRYABLE,
        safe_message="MFDS response body code is not recognized.",
        http_status=http_status,
    )


class MfdsSourceClient:
    """Fail-closed client for one verified MFDS endpoint contract."""

    def __init__(
        self,
        *,
        contract: EndpointContract,
        secret_parameter_name: str,
        secret_value: str,
        decoder: ResponseDecoder,
        client: httpx.AsyncClient,
        resolver: HostResolver = resolve_host,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._contract = contract
        self._secret_parameter_name = secret_parameter_name
        self._secret_value = secret_value
        self._decoder = decoder
        self._client = client
        self._resolver = resolver
        self._sleep = sleep

    async def fetch_one_page(
        self,
        request: SourceRequest,
        *,
        page_number: int | None = None,
    ) -> SourceRunResult:
        """Fetch one page for Local probe or synthetic verification."""

        try:
            provider_request = await self._prepare_request(
                request,
                page_number=page_number,
            )
            return await self._send_and_process(
                provider_request,
                page_number=page_number,
            )
        except SourceSecurityError as error:
            return self._failed_result(
                code=error.code,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message=error.safe_message,
            )
        except ValueError:
            return self._failed_result(
                code=SourceFailureCode.INVALID_REQUEST,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message="Source request parameters are invalid.",
            )
        except httpx.TimeoutException:
            return self._failed_result(
                code=SourceFailureCode.TIMEOUT,
                retry=RetryDisposition.BACKOFF,
                safe_message="MFDS request timed out.",
            )
        except httpx.RequestError:
            return self._failed_result(
                code=SourceFailureCode.PROVIDER_UNAVAILABLE,
                retry=RetryDisposition.BACKOFF,
                safe_message="MFDS provider could not be reached.",
            )

    async def fetch_all_pages(
        self,
        request: SourceRequest,
    ) -> SourceRunResult:
        """Fetch every page atomically within the total execution timeout."""

        try:
            async with asyncio.timeout(self._contract.limits.total_timeout_seconds):
                return await self._fetch_all_pages_within_deadline(request)
        except TimeoutError:
            return self._failed_result(
                code=SourceFailureCode.RETRY_BUDGET_EXHAUSTED,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message="MFDS acquisition exceeded its total time budget.",
            )

    async def _fetch_all_pages_within_deadline(
        self,
        request: SourceRequest,
    ) -> SourceRunResult:
        collected_pages: list[ProviderPage] = []
        first_page = self._contract.pagination.page_base

        for page_offset in range(self._contract.limits.max_pages):
            page_number = first_page + page_offset
            page_result = await self._fetch_page_with_backoff(
                request,
                page_number=page_number,
            )

            if page_result.status is not SourceRunStatus.SUCCEEDED:
                return page_result

            collected_pages.extend(page_result.pages)
            decision = self._pagination_decision(collected_pages)

            if decision is _PaginationDecision.INVALID:
                return self._failed_result(
                    code=SourceFailureCode.SCHEMA_DRIFT,
                    retry=RetryDisposition.NOT_RETRYABLE,
                    safe_message="MFDS pagination metadata is inconsistent.",
                    status=SourceRunStatus.SCHEMA_DRIFT,
                )

            if decision is _PaginationDecision.COMPLETE:
                return self._finalize_pages(collected_pages)

        return self._failed_result(
            code=SourceFailureCode.PAGE_LIMIT_EXCEEDED,
            retry=RetryDisposition.NOT_RETRYABLE,
            safe_message="MFDS acquisition exceeded the maximum page limit.",
        )

    async def _fetch_page_with_backoff(
        self,
        request: SourceRequest,
        *,
        page_number: int,
    ) -> SourceRunResult:
        max_retries = self._contract.limits.max_retry_attempts

        for attempt in range(max_retries + 1):
            result = await self.fetch_one_page(
                request,
                page_number=page_number,
            )
            failure = result.failure

            if failure is None or failure.retry is not RetryDisposition.BACKOFF:
                return result

            if attempt == max_retries:
                return self._failed_result(
                    code=SourceFailureCode.RETRY_BUDGET_EXHAUSTED,
                    retry=RetryDisposition.NOT_RETRYABLE,
                    safe_message="MFDS retry budget was exhausted.",
                    http_status=failure.http_status,
                )

            backoff_seconds = min(0.1 * (2**attempt), 1.0)
            await self._sleep(backoff_seconds)

        raise AssertionError("retry loop must return a result")

    def _pagination_decision(
        self,
        pages: list[ProviderPage],
    ) -> _PaginationDecision:
        observed_totals = {page.total_count for page in pages}

        if len(observed_totals) != 1:
            return _PaginationDecision.INVALID

        record_count = sum(len(page.records) for page in pages)
        total_count = pages[-1].total_count

        if total_count is not None:
            if total_count < 0 or record_count > total_count:
                return _PaginationDecision.INVALID

            if record_count == total_count:
                return _PaginationDecision.COMPLETE

            return _PaginationDecision.CONTINUE

        if len(pages[-1].records) < self._contract.pagination.page_size_limit:
            return _PaginationDecision.COMPLETE

        return _PaginationDecision.CONTINUE

    def _finalize_pages(
        self,
        pages: list[ProviderPage],
    ) -> SourceRunResult:
        if not self._primary_keys_are_valid(pages):
            return self._failed_result(
                code=SourceFailureCode.SCHEMA_DRIFT,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message="MFDS primary key validation failed.",
                status=SourceRunStatus.SCHEMA_DRIFT,
            )

        return SourceRunResult(
            operation=self._contract.identity,
            status=SourceRunStatus.SUCCEEDED,
            pages=tuple(pages),
            failure=None,
        )

    def _primary_keys_are_valid(
        self,
        pages: list[ProviderPage],
    ) -> bool:
        if not self._contract.primary_key_fields:
            return False

        observed_keys: set[tuple[str, ...]] = set()

        for page in pages:
            for record in page.records:
                key_parts: list[str] = []

                for field_name in self._contract.primary_key_fields:
                    value = record.get(field_name)

                    if value is None or value == "":
                        return False

                    key_parts.append(
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )

                primary_key = tuple(key_parts)

                if primary_key in observed_keys:
                    return False

                observed_keys.add(primary_key)

        return True

    async def _prepare_request(
        self,
        request: SourceRequest,
        *,
        page_number: int | None,
    ) -> httpx.Request:
        if request.operation != self._contract.identity:
            raise ValueError

        parameters = self._build_parameters(
            request,
            page_number=page_number,
        )
        operation_url = f"{self._contract.scheme}://{self._contract.host}{self._contract.path_template}"

        await validate_operation_url(
            operation_url,
            contract=self._contract,
            resolver=self._resolver,
        )

        return self._client.build_request(
            self._contract.method,
            operation_url,
            params=parameters,
            timeout=self._request_timeout(),
        )

    async def _send_and_process(
        self,
        provider_request: httpx.Request,
        *,
        page_number: int | None,
    ) -> SourceRunResult:
        response = await self._send_with_validated_redirects(provider_request)

        try:
            return await self._process_response(
                response,
                page_number=page_number,
            )
        finally:
            await response.aclose()

    async def _send_with_validated_redirects(
        self,
        provider_request: httpx.Request,
    ) -> httpx.Response:
        current_request = provider_request
        redirect_hop = 0

        while True:
            response = await self._client.send(
                current_request,
                stream=True,
                follow_redirects=False,
            )

            if response.status_code not in {301, 302, 303, 307, 308}:
                return response

            location = response.headers.get("location")
            if location is None:
                return response

            redirect_hop += 1

            try:
                redirected_url = await validate_redirect_location(
                    current_url=str(current_request.url),
                    location=location,
                    hop_number=redirect_hop,
                    contract=self._contract,
                    resolver=self._resolver,
                )
            finally:
                await response.aclose()

            # 검증된 동일 Endpoint에만 기존 승인 파라미터를 다시 전달합니다.
            # Provider가 Location에 임의 query를 넣어도 그대로 사용하지 않습니다.
            redirected_base_url = httpx.URL(redirected_url).copy_with(query=None)
            current_request = self._client.build_request(
                self._contract.method,
                redirected_base_url,
                params=current_request.url.params,
                timeout=self._request_timeout(),
            )

    def _request_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self._contract.limits.total_timeout_seconds,
            connect=self._contract.limits.connect_timeout_seconds,
            read=self._contract.limits.read_timeout_seconds,
        )

    async def _process_response(
        self,
        response: httpx.Response,
        *,
        page_number: int | None,
    ) -> SourceRunResult:
        http_failure = self._classify_http_status(response.status_code)
        if http_failure is not None:
            return SourceRunResult(
                operation=self._contract.identity,
                status=SourceRunStatus.FAILED,
                pages=(),
                failure=http_failure,
            )

        media_type = validate_response_content_type(
            response.headers.get("content-type"),
            contract=self._contract,
        )
        body = await self._read_limited_body(response)

        if media_type in {"application/xml", "text/xml"}:
            reject_unsafe_xml(body)

        try:
            decoded_page = self._decoder(body, media_type)
        except Exception:
            return self._failed_result(
                code=SourceFailureCode.SCHEMA_DRIFT,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message="MFDS response schema could not be decoded.",
                http_status=response.status_code,
                status=SourceRunStatus.SCHEMA_DRIFT,
            )

        body_failure = classify_body_code(
            decoded_page.body_code,
            contract=self._contract,
            http_status=response.status_code,
            retry_reset_at=decoded_page.retry_reset_at,
        )
        if body_failure is not None:
            failure_status = (
                SourceRunStatus.SCHEMA_DRIFT
                if body_failure.code is SourceFailureCode.SCHEMA_DRIFT
                else SourceRunStatus.FAILED
            )
            return SourceRunResult(
                operation=self._contract.identity,
                status=failure_status,
                pages=(),
                failure=body_failure,
            )

        if not decoded_page.records:
            return self._failed_result(
                code=SourceFailureCode.EMPTY_RESULT,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message="MFDS response contained no records.",
                http_status=response.status_code,
            )

        actual_page_number = page_number if page_number is not None else self._contract.pagination.page_base
        page = ProviderPage(
            page_number=actual_page_number,
            records=decoded_page.records,
            response_checksum=hashlib.sha256(body).hexdigest(),
            content_type=media_type,
            total_count=decoded_page.total_count,
        )

        return SourceRunResult(
            operation=self._contract.identity,
            status=SourceRunStatus.SUCCEEDED,
            pages=(page,),
            failure=None,
        )

    def _build_parameters(
        self,
        request: SourceRequest,
        *,
        page_number: int | None,
    ) -> dict[str, str | int]:
        required_parameters = {parameter.name: parameter for parameter in self._contract.required_parameters}
        sensitive_names = {parameter.name for parameter in self._contract.required_parameters if parameter.sensitive}
        pagination_names = {
            self._contract.pagination.page_parameter,
            self._contract.pagination.page_size_parameter,
        }
        allowed_names = set(required_parameters) | pagination_names
        provided_names = set(request.parameters)

        if provided_names - allowed_names:
            raise ValueError

        if provided_names & sensitive_names:
            raise ValueError

        if self._secret_parameter_name not in sensitive_names or not self._secret_value.strip():
            raise ValueError

        auto_parameters = sensitive_names | pagination_names
        missing_parameters = {
            name for name in required_parameters if name not in provided_names and name not in auto_parameters
        }
        if missing_parameters:
            raise ValueError

        parameters = dict(request.parameters)
        parameters[self._secret_parameter_name] = self._secret_value
        parameters[self._contract.pagination.page_parameter] = (
            page_number if page_number is not None else self._contract.pagination.page_base
        )
        parameters[self._contract.pagination.page_size_parameter] = self._contract.pagination.page_size_limit

        return parameters

    async def _read_limited_body(
        self,
        response: httpx.Response,
    ) -> bytes:
        chunks: list[bytes] = []
        decompressed_size = 0

        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            decompressed_size += len(chunk)

            validate_body_sizes(
                raw_size=response.num_bytes_downloaded,
                decompressed_size=decompressed_size,
                limits=self._contract.limits,
            )

        return b"".join(chunks)

    def _classify_http_status(
        self,
        http_status: int,
    ) -> SourceClientFailure | None:
        if http_status == 200:
            return None

        if http_status in {401, 403}:
            return SourceClientFailure(
                code=SourceFailureCode.AUTHENTICATION_FAILED,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message="MFDS authentication failed.",
                http_status=http_status,
            )

        if http_status == 429:
            return SourceClientFailure(
                code=SourceFailureCode.RATE_LIMITED,
                retry=RetryDisposition.BACKOFF,
                safe_message="MFDS request was rate limited.",
                http_status=http_status,
            )

        if http_status >= 500:
            return SourceClientFailure(
                code=SourceFailureCode.PROVIDER_UNAVAILABLE,
                retry=RetryDisposition.BACKOFF,
                safe_message="MFDS provider is temporarily unavailable.",
                http_status=http_status,
            )

        if 300 <= http_status < 400:
            return SourceClientFailure(
                code=SourceFailureCode.REDIRECT_REJECTED,
                retry=RetryDisposition.NOT_RETRYABLE,
                safe_message="MFDS redirect was not followed.",
                http_status=http_status,
            )

        return SourceClientFailure(
            code=SourceFailureCode.REQUEST_REJECTED,
            retry=RetryDisposition.NOT_RETRYABLE,
            safe_message="MFDS request was rejected.",
            http_status=http_status,
        )

    def _failed_result(
        self,
        *,
        code: SourceFailureCode,
        retry: RetryDisposition,
        safe_message: str,
        http_status: int | None = None,
        status: SourceRunStatus = SourceRunStatus.FAILED,
    ) -> SourceRunResult:
        return SourceRunResult(
            operation=self._contract.identity,
            status=status,
            pages=(),
            failure=SourceClientFailure(
                code=code,
                retry=retry,
                safe_message=safe_message,
                http_status=http_status,
            ),
        )
