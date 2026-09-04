from ai_worker.tasks.rag.source_client.contracts import (
    P0_OPERATIONS,
    ProviderPage,
    RetryDisposition,
    SourceClientFailure,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRunResult,
    SourceRunStatus,
)


def test_p0_operations_use_the_frozen_stable_codes() -> None:
    stable_codes = {
        (
            operation.source_code,
            operation.endpoint_code,
            operation.operation_code,
        )
        for operation in P0_OPERATIONS
    }

    assert stable_codes == {
        (
            "MFDS_PRODUCT_APPROVAL",
            "MFDS_PRODUCT_APPROVAL_API",
            "LIST_APPROVED_PRODUCTS",
        ),
        (
            "MFDS_DUR",
            "MFDS_DUR_INGREDIENT_API",
            "LIST_INGREDIENT_CONTRAINDICATIONS",
        ),
        (
            "MFDS_PATIENT_MEDICATION_GUIDE",
            "MFDS_PATIENT_GUIDE_API",
            "LIST_PATIENT_MEDICATION_GUIDES",
        ),
    }


def test_successful_non_empty_run_allows_snapshot_candidate() -> None:
    operation = P0_OPERATIONS[0]
    page = ProviderPage(
        page_number=1,
        records=({"synthetic_id": "product-1"},),
        response_checksum="a" * 64,
        content_type="application/json",
    )

    result = SourceRunResult(
        operation=operation,
        status=SourceRunStatus.SUCCEEDED,
        pages=(page,),
        failure=None,
    )

    assert result.record_count == 1
    assert result.snapshot_candidate_allowed is True


def test_failed_run_does_not_expose_partial_pages_as_snapshot_candidate() -> None:
    operation = SourceOperationIdentity(
        source_code="MFDS_PRODUCT_APPROVAL",
        endpoint_code="MFDS_PRODUCT_APPROVAL_API",
        operation_code="LIST_APPROVED_PRODUCTS",
    )
    failure = SourceClientFailure(
        code=SourceFailureCode.PROVIDER_UNAVAILABLE,
        retry=RetryDisposition.BACKOFF,
        safe_message="MFDS provider request failed.",
        http_status=503,
    )

    result = SourceRunResult(
        operation=operation,
        status=SourceRunStatus.FAILED,
        pages=(),
        failure=failure,
    )

    assert result.record_count == 0
    assert result.snapshot_candidate_allowed is False


def test_http_200_body_authentication_failure_is_not_retryable() -> None:
    failure = SourceClientFailure(
        code=SourceFailureCode.AUTHENTICATION_FAILED,
        retry=RetryDisposition.NOT_RETRYABLE,
        safe_message="MFDS authentication failed.",
        http_status=200,
    )

    assert failure.retry is RetryDisposition.NOT_RETRYABLE
    assert "secret" not in failure.safe_message.lower()
    assert "servicekey" not in failure.safe_message.lower()


def test_daily_limit_requires_an_explicit_reset_time() -> None:
    failure = SourceClientFailure(
        code=SourceFailureCode.RATE_LIMITED,
        retry=RetryDisposition.RETRY_AT_RESET,
        safe_message="MFDS daily request limit was reached.",
        http_status=200,
        retry_reset_at="2026-09-05T00:00:00+09:00",
    )

    assert failure.retry is RetryDisposition.RETRY_AT_RESET
    assert failure.retry_reset_at is not None
