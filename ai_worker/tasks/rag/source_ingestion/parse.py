"""검증된 수집 결과에서 정규화 입력과 제품 checksum을 준비합니다."""

from copy import deepcopy
from pathlib import Path

from ai_worker.tasks.rag.source_client.contracts import (
    SourceOperationIdentity,
    SourceRunResult,
)
from ai_worker.tasks.rag.source_client.receipts import EndpointReceipt
from ai_worker.tasks.rag.source_ingestion.checksums import (
    product_canonical_checksum,
)
from ai_worker.tasks.rag.source_ingestion.receipt_validation import (
    ProductReceiptEvidence,
    load_product_endpoint_receipt,
    verify_receipt_fixture_evidence,
)
from ai_worker.tasks.rag.source_ingestion.validation import (
    require_complete_source_run,
    require_receipt_compatibility,
)

_PRODUCT_OPERATION = SourceOperationIdentity(
    source_code="MFDS_PRODUCT_APPROVAL",
    endpoint_code="MFDS_PRODUCT_APPROVAL_API",
    operation_code="LIST_APPROVED_PRODUCTS",
)
PRODUCT_CANONICALIZATION_SPEC_VERSION = "mfds-product-approval@1"


def _copy_parser_records(
    result: SourceRunResult,
) -> tuple[dict[str, object], ...]:
    """원본 수집 결과와 분리된 Parser 입력을 만듭니다."""
    return tuple(deepcopy(dict(record)) for page in result.pages for record in page.records)


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

    return _copy_parser_records(result)


def collect_product_records_from_evidence(
    *,
    result: SourceRunResult,
    evidence: ProductReceiptEvidence,
) -> tuple[dict[str, object], ...]:
    """이미 검증된 제품 Receipt 증빙으로 Parser 입력을 만듭니다."""
    require_complete_source_run(result)

    if evidence.identity != result.operation:
        raise ValueError("Endpoint receipt identity does not match source run.")

    return _copy_parser_records(result)


def collect_verified_product_records(
    *,
    result: SourceRunResult,
    receipt_path: Path,
    repository_root: Path,
) -> tuple[dict[str, object], ...]:
    """파일 무결성까지 검증한 제품 Receipt로 Parser 입력을 만듭니다."""
    evidence = load_product_endpoint_receipt(receipt_path)

    verify_receipt_fixture_evidence(
        evidence=evidence.fixture_evidence,
        repository_root=repository_root,
    )
    return collect_product_records_from_evidence(
        result=result,
        evidence=evidence,
    )


def calculate_verified_product_run_checksum(
    *,
    result: SourceRunResult,
    receipt_path: Path,
    repository_root: Path,
) -> str:
    """검증된 Receipt와 fixture를 통과한 제품 수집 결과의 checksum을 계산합니다."""
    records = collect_verified_product_records(
        result=result,
        receipt_path=receipt_path,
        repository_root=repository_root,
    )

    return product_canonical_checksum(records)


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
