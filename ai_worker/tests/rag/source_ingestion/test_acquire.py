import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    P0_OPERATIONS,
    PrimaryKeyValidationResult,
    ProviderPage,
    SourceOperationIdentity,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.mfds_client import DecodedProviderPage
from ai_worker.tasks.rag.source_ingestion.acquire import (
    preserve_raw_artifacts,
    preserve_rejection_artifact,
    verify_raw_artifact_manifest,
    verify_source_run_artifacts,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    RawArtifactStore,
    StoredRawArtifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import (
    raw_manifest_checksum,
)
from ai_worker.tasks.rag.source_ingestion.reject_codes import (
    PRODUCT_REJECT_IDENTITY,
    REJECT_CODE_CONTRACT_VERSION,
    RejectContractError,
)


def _decode_synthetic_page(
    body: bytes,
    media_type: str,
) -> DecodedProviderPage:
    assert media_type == "application/json"
    payload = json.loads(body)
    assert isinstance(payload, dict)
    page_number = payload["page"]
    assert type(page_number) is int

    return DecodedProviderPage(
        body_code="00",
        records=({"ITEM_SEQ": f"synthetic-product-{page_number:03d}"},),
        page_number=page_number,
        page_size=1,
        total_count=2,
    )


def _write_artifact(
    directory: Path,
    name: str,
    content: bytes,
) -> tuple[Path, RawArtifactMetadata]:
    file_path = directory / name
    file_path.write_bytes(content)

    metadata = RawArtifactMetadata(
        artifact_key=f"synthetic/{name}",
        raw_checksum=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/json",
    )

    return file_path, metadata


class RecordingArtifactStore(RawArtifactStore):
    def __init__(self) -> None:
        self.pages: list[int] = []

    def put_verified(
        self,
        *,
        page_number: int | None,
        file_path: Path,
        metadata: RawArtifactMetadata,
        artifact_kind: IngestionArtifactKind = IngestionArtifactKind.RAW_RESPONSE,
        reject_code: str | None = None,
        parser_location: str | None = None,
    ) -> StoredRawArtifact:
        assert file_path.exists()
        if page_number is not None:
            self.pages.append(page_number)
        return StoredRawArtifact(
            page_number=page_number,
            metadata=metadata,
            storage_backend="SYNTHETIC_PRIVATE",
            object_key=f"synthetic/{metadata.raw_checksum}",
            artifact_kind=artifact_kind,
            reject_code=reject_code,
            parser_location=parser_location,
        )


def test_preserves_raw_artifacts_in_page_order(tmp_path: Path) -> None:
    first = _write_artifact(tmp_path, "page-1.json", b'{"page":1}')
    second = _write_artifact(tmp_path, "page-2.json", b'{"page":2}')
    store = RecordingArtifactStore()

    stored = preserve_raw_artifacts(
        artifacts=[
            (2, second[0], second[1]),
            (1, first[0], first[1]),
        ],
        store=store,
    )

    assert store.pages == [1, 2]
    assert [artifact.page_number for artifact in stored] == [1, 2]


def test_duplicate_page_is_rejected_before_storage(tmp_path: Path) -> None:
    first = _write_artifact(tmp_path, "page-1.json", b'{"page":1}')
    second = _write_artifact(tmp_path, "page-2.json", b'{"page":2}')
    store = RecordingArtifactStore()

    with pytest.raises(ValueError, match="duplicate page binding"):
        preserve_raw_artifacts(
            artifacts=[
                (1, first[0], first[1]),
                (1, second[0], second[1]),
            ],
            store=store,
        )

    assert store.pages == []


def test_preserves_rejection_with_safe_metadata(tmp_path: Path) -> None:
    rejection = _write_artifact(tmp_path, "reject-1.json", b'{"ITEM_SEQ":null}')
    store = RecordingArtifactStore()

    stored = preserve_rejection_artifact(
        file_path=rejection[0],
        metadata=rejection[1],
        reject_code="ITEM_SEQ_REQUIRED",
        parser_location="page[1].record[3]",
        store=store,
        identity=PRODUCT_REJECT_IDENTITY,
        reject_code_contract_version=REJECT_CODE_CONTRACT_VERSION,
    )

    assert stored.artifact_kind is IngestionArtifactKind.REJECTS
    assert stored.page_number is None
    assert stored.reject_code == "ITEM_SEQ_REQUIRED"
    assert stored.parser_location == "page[1].record[3]"


def test_rejection_artifact_outside_contract_scope_is_refused(tmp_path: Path) -> None:
    """적용 범위 밖 Operation의 거부 원문은 보존 전에 차단되어야 한다."""
    rejection = _write_artifact(tmp_path, "reject-1.json", b'{"ITEM_SEQ":null}')
    store = RecordingArtifactStore()
    outside = SourceOperationIdentity("SYNTHETIC_SOURCE", "SYNTHETIC_ENDPOINT", "SYNTHETIC_OPERATION")

    with pytest.raises(RejectContractError):
        preserve_rejection_artifact(
            file_path=rejection[0],
            metadata=rejection[1],
            reject_code="ITEM_SEQ_REQUIRED",
            parser_location="page[1].record[3]",
            store=store,
            identity=outside,
            reject_code_contract_version=REJECT_CODE_CONTRACT_VERSION,
        )

    assert store.pages == []


def _complete_run(
    first_content: bytes,
    second_content: bytes,
) -> SourceRunResult:
    return SourceRunResult(
        operation=P0_OPERATIONS[0],
        status=SourceRunStatus.SUCCEEDED,
        pages=(
            ProviderPage(
                page_number=1,
                records=({"ITEM_SEQ": "synthetic-product-001"},),
                response_checksum=hashlib.sha256(first_content).hexdigest(),
                content_type="application/json",
                total_count=2,
            ),
            ProviderPage(
                page_number=2,
                records=({"ITEM_SEQ": "synthetic-product-002"},),
                response_checksum=hashlib.sha256(second_content).hexdigest(),
                content_type="application/json",
                total_count=2,
            ),
        ),
        failure=None,
        primary_key_validation=PrimaryKeyValidationResult(
            passed=True,
            record_count=2,
            null_count=0,
            duplicate_count=0,
        ),
        full_scan_completed=True,
    )


def test_verifies_all_files_and_preserves_manifest_order_independence(
    tmp_path: Path,
) -> None:
    first = _write_artifact(tmp_path, "a.json", b'{"value":1}')
    second = _write_artifact(tmp_path, "b.json", b'{"value":2}')
    expected = raw_manifest_checksum([first[1], second[1]])

    assert verify_raw_artifact_manifest([first, second]) == expected
    assert verify_raw_artifact_manifest(iter([second, first])) == expected


@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_rejects_manifest_when_later_file_fails(
    tmp_path: Path,
    failure: str,
) -> None:
    first = _write_artifact(tmp_path, "a.json", b"abc")
    second = _write_artifact(tmp_path, "b.json", b"def")

    if failure == "missing":
        second[0].unlink()
        expected_message = "could not be read"
    else:
        # 크기는 유지하고 내용만 변경합니다.
        second[0].write_bytes(b"xyz")
        expected_message = "checksum mismatch"

    with pytest.raises(ValueError, match=expected_message):
        verify_raw_artifact_manifest([first, second])


def test_rejects_empty_manifest() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        verify_raw_artifact_manifest([])


def test_rejects_duplicate_keys_before_reading_files(tmp_path: Path) -> None:
    metadata = RawArtifactMetadata(
        artifact_key="synthetic/duplicate.json",
        raw_checksum=hashlib.sha256(b"abc").hexdigest(),
        byte_size=3,
        content_type="application/json",
    )

    # 파일이 없어도 먼저 중복 Key 오류로 거부해야 합니다.
    entries = [
        (tmp_path / "missing-a.json", metadata),
        (tmp_path / "missing-b.json", metadata),
    ]

    with pytest.raises(ValueError, match="Duplicate"):
        verify_raw_artifact_manifest(entries)


def test_verifies_source_run_page_artifact_bindings(
    tmp_path: Path,
) -> None:
    first_content = b'{"page":1}'
    second_content = b'{"page":2}'
    first = _write_artifact(
        tmp_path,
        "page-1.json",
        first_content,
    )
    second = _write_artifact(
        tmp_path,
        "page-2.json",
        second_content,
    )
    result = _complete_run(
        first_content,
        second_content,
    )
    expected = raw_manifest_checksum([first[1], second[1]])

    actual = verify_source_run_artifacts(
        result=result,
        artifacts=[
            (2, second[0], second[1]),
            (1, first[0], first[1]),
        ],
        decoder=_decode_synthetic_page,
        success_codes=("00",),
    )

    assert actual.raw_manifest_checksum == expected
    assert actual.records == (
        {"ITEM_SEQ": "synthetic-product-001"},
        {"ITEM_SEQ": "synthetic-product-002"},
    )


def test_rejects_missing_source_page_artifact(
    tmp_path: Path,
) -> None:
    first_content = b'{"page":1}'
    second_content = b'{"page":2}'
    first = _write_artifact(
        tmp_path,
        "page-1.json",
        first_content,
    )
    result = _complete_run(
        first_content,
        second_content,
    )

    with pytest.raises(ValueError, match="do not match"):
        verify_source_run_artifacts(
            result=result,
            artifacts=[
                (1, first[0], first[1]),
            ],
            decoder=_decode_synthetic_page,
            success_codes=("00",),
        )


def test_rejects_duplicate_source_page_binding(
    tmp_path: Path,
) -> None:
    first_content = b'{"page":1}'
    second_content = b'{"page":2}'
    first = _write_artifact(
        tmp_path,
        "page-1.json",
        first_content,
    )
    second = _write_artifact(
        tmp_path,
        "page-2.json",
        second_content,
    )
    result = _complete_run(
        first_content,
        second_content,
    )

    with pytest.raises(ValueError, match="duplicate page binding"):
        verify_source_run_artifacts(
            result=result,
            artifacts=[
                (1, first[0], first[1]),
                (1, second[0], second[1]),
            ],
            decoder=_decode_synthetic_page,
            success_codes=("00",),
        )


@pytest.mark.parametrize(
    "changed_field",
    [
        "checksum",
        "content_type",
    ],
)
def test_rejects_source_page_artifact_metadata_mismatch(
    tmp_path: Path,
    changed_field: str,
) -> None:
    first_content = b'{"page":1}'
    second_content = b'{"page":2}'
    first = _write_artifact(
        tmp_path,
        "page-1.json",
        first_content,
    )
    second = _write_artifact(
        tmp_path,
        "page-2.json",
        second_content,
    )
    result = _complete_run(
        first_content,
        second_content,
    )

    if changed_field == "checksum":
        first_page = replace(
            result.pages[0],
            response_checksum="f" * 64,
        )
        expected_message = "checksum does not match"
    else:
        first_page = replace(
            result.pages[0],
            content_type="application/xml",
        )
        expected_message = "content type does not match"

    changed_result = replace(
        result,
        pages=(
            first_page,
            result.pages[1],
        ),
    )

    with pytest.raises(ValueError, match=expected_message):
        verify_source_run_artifacts(
            result=changed_result,
            artifacts=[
                (1, first[0], first[1]),
                (2, second[0], second[1]),
            ],
            decoder=_decode_synthetic_page,
            success_codes=("00",),
        )


def test_rejects_raw_artifact_response_page_mismatch(
    tmp_path: Path,
) -> None:
    mismatched_first_content = b'{"page":2}'
    second_content = b'{"page":2}'
    first = _write_artifact(
        tmp_path,
        "page-1.json",
        mismatched_first_content,
    )
    second = _write_artifact(
        tmp_path,
        "page-2.json",
        second_content,
    )
    result = _complete_run(
        mismatched_first_content,
        second_content,
    )

    with pytest.raises(ValueError, match="page number does not match"):
        verify_source_run_artifacts(
            result=result,
            artifacts=[
                (1, first[0], first[1]),
                (2, second[0], second[1]),
            ],
            decoder=_decode_synthetic_page,
            success_codes=("00",),
        )
