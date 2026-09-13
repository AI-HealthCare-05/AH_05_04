"""Parser 입력에 사용할 수집 결과의 완전성을 검증합니다."""

from ai_worker.tasks.rag.source_client.contracts import (
    EndpointExecutionStatus,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.receipts import EndpointReceipt


def require_complete_source_run(result: SourceRunResult) -> None:
    """전체 수집·기본키 검증을 통과하지 못한 결과를 거부합니다."""
    if not result.snapshot_candidate_allowed:
        raise ValueError("Source run is not eligible for parser input.")


def require_receipt_compatibility(
    *,
    result: SourceRunResult,
    receipt: EndpointReceipt,
) -> None:
    """수집 결과와 Endpoint 검증 증빙의 상태·식별자를 확인합니다."""
    require_complete_source_run(result)

    if receipt.identity != result.operation:
        raise ValueError("Endpoint receipt identity does not match source run.")

    if (
        receipt.execution_status is not EndpointExecutionStatus.COMPLETED
        or receipt.source_run_status is not SourceRunStatus.SUCCEEDED
        or receipt.parser_activation_allowed is not True
        or receipt.failure_code is not None
        or receipt.blocking_code is not None
    ):
        raise ValueError("Endpoint receipt does not permit parser input.")

    if (
        not receipt.primary_key_fields
        or receipt.validated_record_count is None
        or receipt.validated_record_count <= 0
        or receipt.primary_key_null_count != 0
        or receipt.primary_key_duplicate_count != 0
    ):
        raise ValueError("Endpoint receipt has no valid primary-key evidence.")
