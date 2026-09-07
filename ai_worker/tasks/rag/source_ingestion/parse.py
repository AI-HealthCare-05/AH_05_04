"""검증된 수집 결과에서 정규화 입력과 제품 checksum을 준비합니다."""

from copy import deepcopy

from ai_worker.tasks.rag.source_client.contracts import (
    SourceOperationIdentity,
    SourceRunResult,
)
from ai_worker.tasks.rag.source_client.receipts import EndpointReceipt
from ai_worker.tasks.rag.source_ingestion.checksums import (
    product_canonical_checksum,
)
from ai_worker.tasks.rag.source_ingestion.validation import (
    require_receipt_compatibility,
)

_PRODUCT_OPERATION = SourceOperationIdentity(
    source_code="MFDS_PRODUCT_APPROVAL",
    endpoint_code="MFDS_PRODUCT_APPROVAL_API",
    operation_code="LIST_APPROVED_PRODUCTS",
)


def collect_parser_records(
    *,
    result: SourceRunResult,
    receipt: EndpointReceipt,
) -> tuple[dict[str, object], ...]:
    """전체 수집과 Receipt 검증 후 원본과 분리된 레코드를 반환합니다."""
    require_receipt_compatibility(
        result=result,
        receipt=receipt,
    )

    return tuple(deepcopy(dict(record)) for page in result.pages for record in page.records)


def calculate_product_run_checksum(
    *,
    result: SourceRunResult,
    receipt: EndpointReceipt,
) -> str:
    """검증된 제품 수집 결과 전체의 canonical checksum을 계산합니다."""
    if result.operation != _PRODUCT_OPERATION:
        raise ValueError("Only the product approval operation is supported.")

    if receipt.primary_key_fields != ("ITEM_SEQ",):
        raise ValueError("Product receipt primary key must be ITEM_SEQ.")

    records = collect_parser_records(
        result=result,
        receipt=receipt,
    )

    return product_canonical_checksum(records)
