from dataclasses import replace

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    P0_OPERATIONS,
    EndpointExecutionStatus,
    PrimaryKeyValidationResult,
    ProviderPage,
    RetryDisposition,
    SourceClientFailure,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.probe import build_live_receipt
from ai_worker.tasks.rag.source_client.receipts import EndpointReceipt
from ai_worker.tasks.rag.source_ingestion.validation import (
    require_complete_source_run,
    require_receipt_compatibility,
)


def _complete_run() -> SourceRunResult:
    return SourceRunResult(
        operation=P0_OPERATIONS[0],
        status=SourceRunStatus.SUCCEEDED,
        pages=(
            ProviderPage(
                page_number=1,
                records=({"ITEM_SEQ": "synthetic-product-001"},),
                response_checksum="a" * 64,
                content_type="application/json",
                total_count=1,
            ),
        ),
        failure=None,
        primary_key_validation=PrimaryKeyValidationResult(
            passed=True,
            record_count=1,
            null_count=0,
            duplicate_count=0,
        ),
        full_scan_completed=True,
    )


def test_accepts_complete_source_run() -> None:
    require_complete_source_run(_complete_run())


@pytest.mark.parametrize(
    "result",
    [
        replace(_complete_run(), status=SourceRunStatus.FAILED),
        replace(_complete_run(), status=SourceRunStatus.SCHEMA_DRIFT),
        replace(_complete_run(), full_scan_completed=False),
        replace(_complete_run(), pages=()),
        replace(_complete_run(), primary_key_validation=None),
        replace(
            _complete_run(),
            primary_key_validation=PrimaryKeyValidationResult(
                passed=False,
                record_count=1,
                null_count=0,
                duplicate_count=1,
            ),
        ),
        replace(
            _complete_run(),
            primary_key_validation=PrimaryKeyValidationResult(
                passed=True,
                record_count=2,
                null_count=0,
                duplicate_count=0,
            ),
        ),
        replace(
            _complete_run(),
            failure=SourceClientFailure(
                code=SourceFailureCode.TIMEOUT,
                retry=RetryDisposition.BACKOFF,
                safe_message="Synthetic timeout.",
            ),
        ),
    ],
)
def test_rejects_ineligible_source_run(result: SourceRunResult) -> None:
    with pytest.raises(
        ValueError,
        match="Source run is not eligible for parser input.",
    ):
        require_complete_source_run(result)


def _synthetic_receipt() -> EndpointReceipt:
    # 기존 builder로 테스트용 객체만 생성합니다.
    # 외부 호출이나 실제 검증 증빙 저장은 수행하지 않습니다.
    return build_live_receipt(
        operation_code="LIST_APPROVED_PRODUCTS",
        result=_complete_run(),
        validated_at="2026-09-07T00:00:00+00:00",
        git_sha="synthetic-test-only",
    )


def test_accepts_matching_receipt() -> None:
    require_receipt_compatibility(
        result=_complete_run(),
        receipt=_synthetic_receipt(),
    )


def test_receipt_count_can_differ_from_current_run() -> None:
    receipt = replace(
        _synthetic_receipt(),
        validated_record_count=100,
    )

    require_receipt_compatibility(
        result=_complete_run(),
        receipt=receipt,
    )


@pytest.mark.parametrize(
    "identity",
    [
        replace(P0_OPERATIONS[0], source_code="OTHER_SOURCE"),
        replace(P0_OPERATIONS[0], endpoint_code="OTHER_ENDPOINT"),
        replace(P0_OPERATIONS[0], operation_code="OTHER_OPERATION"),
    ],
)
def test_rejects_receipt_identity_mismatch(
    identity: SourceOperationIdentity,
) -> None:
    receipt = replace(_synthetic_receipt(), identity=identity)

    with pytest.raises(ValueError, match="identity does not match"):
        require_receipt_compatibility(
            result=_complete_run(),
            receipt=receipt,
        )


@pytest.mark.parametrize(
    "receipt",
    [
        replace(
            _synthetic_receipt(),
            execution_status=EndpointExecutionStatus.NOT_RUN,
        ),
        replace(
            _synthetic_receipt(),
            execution_status=EndpointExecutionStatus.FAILED,
        ),
        replace(
            _synthetic_receipt(),
            source_run_status=SourceRunStatus.SCHEMA_DRIFT,
        ),
        replace(_synthetic_receipt(), parser_activation_allowed=False),
        replace(
            _synthetic_receipt(),
            failure_code=SourceFailureCode.SCHEMA_DRIFT,
        ),
        replace(
            _synthetic_receipt(),
            blocking_code="BLOCKED_BY_UNSTABLE_PRIMARY_KEY",
        ),
        replace(_synthetic_receipt(), primary_key_fields=()),
        replace(_synthetic_receipt(), validated_record_count=None),
        replace(_synthetic_receipt(), validated_record_count=0),
        replace(_synthetic_receipt(), primary_key_null_count=1),
        replace(_synthetic_receipt(), primary_key_duplicate_count=1),
    ],
)
def test_rejects_ineligible_receipt(receipt: EndpointReceipt) -> None:
    with pytest.raises(ValueError, match="Endpoint receipt"):
        require_receipt_compatibility(
            result=_complete_run(),
            receipt=receipt,
        )


def test_receipt_does_not_override_incomplete_run() -> None:
    with pytest.raises(ValueError, match="Source run is not eligible"):
        require_receipt_compatibility(
            result=replace(_complete_run(), full_scan_completed=False),
            receipt=_synthetic_receipt(),
        )
