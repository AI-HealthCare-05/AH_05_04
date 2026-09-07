import hashlib
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    P0_OPERATIONS,
    PrimaryKeyValidationResult,
    ProviderPage,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
)
from ai_worker.tasks.rag.source_ingestion.checksums import (
    product_canonical_checksum,
)
from ai_worker.tasks.rag.source_ingestion.result import (
    build_product_ingestion_result,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
PRODUCT_RECEIPT_PATH = REPOSITORY_ROOT / "docs" / "validation" / "rag" / "endpoints" / "LIST_APPROVED_PRODUCTS.json"


def _source_result(content: bytes) -> SourceRunResult:
    return SourceRunResult(
        operation=P0_OPERATIONS[0],
        status=SourceRunStatus.SUCCEEDED,
        pages=(
            ProviderPage(
                page_number=1,
                records=(
                    {
                        "ITEM_SEQ": "synthetic-product-001",
                        "ITEM_NAME": "합성 의약품",
                    },
                ),
                response_checksum=hashlib.sha256(content).hexdigest(),
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


def _write_artifact(
    directory: Path,
    content: bytes,
) -> tuple[Path, RawArtifactMetadata]:
    file_path = directory / "page-000001.json"
    file_path.write_bytes(content)

    return (
        file_path,
        RawArtifactMetadata(
            artifact_key=("mfds/product-approval/page-000001.json"),
            raw_checksum=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            content_type="application/json",
        ),
    )


def test_builds_verified_product_ingestion_result(
    tmp_path: Path,
) -> None:
    content = b'{"synthetic_page":1}'
    file_path, metadata = _write_artifact(
        tmp_path,
        content,
    )
    source_result = _source_result(content)

    ingestion_result = build_product_ingestion_result(
        result=source_result,
        artifacts=[
            (1, file_path, metadata),
        ],
        receipt_path=PRODUCT_RECEIPT_PATH,
        repository_root=REPOSITORY_ROOT,
    )

    assert ingestion_result.identity == P0_OPERATIONS[0]
    assert len(ingestion_result.endpoint_receipt_hash) == 64
    assert len(ingestion_result.raw_manifest_checksum) == 64
    assert ingestion_result.canonical_checksum == (product_canonical_checksum(source_result.pages[0].records))
    assert ingestion_result.canonicalization_spec_version == "mfds-product-approval@1"
    assert ingestion_result.record_count == 1
    assert ingestion_result.artifact_count == 1


def test_does_not_build_result_from_changed_artifact(
    tmp_path: Path,
) -> None:
    original = b'{"synthetic_page":1}'
    file_path, metadata = _write_artifact(
        tmp_path,
        original,
    )
    source_result = _source_result(original)

    file_path.write_bytes(b'{"synthetic_page":2}')

    with pytest.raises(ValueError, match="checksum mismatch"):
        build_product_ingestion_result(
            result=source_result,
            artifacts=[
                (1, file_path, metadata),
            ],
            receipt_path=PRODUCT_RECEIPT_PATH,
            repository_root=REPOSITORY_ROOT,
        )
