"""검증이 끝난 제품 Source ingestion 결과 계약입니다."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ai_worker.tasks.rag.source_client.contracts import (
    SourceOperationIdentity,
    SourceRunResult,
)
from ai_worker.tasks.rag.source_client.decoders import decode_mfds_json
from ai_worker.tasks.rag.source_client.endpoints import MFDS_ENDPOINT_CANDIDATES
from ai_worker.tasks.rag.source_ingestion.acquire import (
    verify_source_run_artifacts,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
)
from ai_worker.tasks.rag.source_ingestion.checksums import (
    product_canonical_checksum,
)
from ai_worker.tasks.rag.source_ingestion.parse import (
    PRODUCT_CANONICALIZATION_SPEC_VERSION,
)
from ai_worker.tasks.rag.source_ingestion.receipt_validation import (
    load_product_endpoint_receipt,
    verify_receipt_fixture_evidence,
)

_PRODUCT_CONTRACT = MFDS_ENDPOINT_CANDIDATES["LIST_APPROVED_PRODUCTS"].contract


@dataclass(frozen=True, slots=True)
class ProductIngestionResult:
    """#164 저장 인터페이스에 전달할 검증 완료 결과입니다."""

    identity: SourceOperationIdentity
    endpoint_receipt_hash: str
    raw_manifest_checksum: str
    canonical_checksum: str
    canonicalization_spec_version: str
    record_count: int
    artifact_count: int


def build_product_ingestion_result(
    *,
    result: SourceRunResult,
    artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
    receipt_path: Path,
    repository_root: Path,
) -> ProductIngestionResult:
    """검증된 제품 수집 결과와 재현성 증빙을 묶습니다."""
    entries = tuple(artifacts)
    receipt = load_product_endpoint_receipt(receipt_path)

    verify_receipt_fixture_evidence(
        evidence=receipt.fixture_evidence,
        repository_root=repository_root,
    )

    if receipt.identity != result.operation:
        raise ValueError("Endpoint receipt identity does not match source run.")

    verified_artifacts = verify_source_run_artifacts(
        result=result,
        artifacts=entries,
        decoder=decode_mfds_json,
        success_codes=_PRODUCT_CONTRACT.body_codes.success_codes,
    )
    records = verified_artifacts.records
    canonical_checksum = product_canonical_checksum(records)

    return ProductIngestionResult(
        identity=result.operation,
        endpoint_receipt_hash=receipt.receipt_hash,
        raw_manifest_checksum=verified_artifacts.raw_manifest_checksum,
        canonical_checksum=canonical_checksum,
        canonicalization_spec_version=(PRODUCT_CANONICALIZATION_SPEC_VERSION),
        record_count=len(records),
        artifact_count=len(entries),
    )
