import hashlib
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
    verify_raw_artifact,
)


def _metadata(content: bytes) -> RawArtifactMetadata:
    return RawArtifactMetadata(
        artifact_key="synthetic/source.json",
        raw_checksum=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/json",
    )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("artifact_key", "x" * 501, "500"),
        ("content_type", "x" * 256, "255"),
    ],
)
def test_rejects_metadata_larger_than_database_contract(
    field_name: str,
    value: str,
    message: str,
) -> None:
    values: dict[str, object] = {
        "artifact_key": "synthetic/source.json",
        "raw_checksum": "a" * 64,
        "byte_size": 1,
        "content_type": "application/json",
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=message):
        RawArtifactMetadata(
            artifact_key=cast(str, values["artifact_key"]),
            raw_checksum=cast(str, values["raw_checksum"]),
            byte_size=cast(int, values["byte_size"]),
            content_type=cast(str, values["content_type"]),
        )


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b'{"ITEM_SEQ":"synthetic-product-001"}',
        b"x" * (1024 * 1024 + 17),
    ],
)
def test_accepts_matching_file(
    tmp_path: Path,
    content: bytes,
) -> None:
    file_path = tmp_path / "source.json"
    file_path.write_bytes(content)

    verify_raw_artifact(
        file_path=file_path,
        metadata=_metadata(content),
    )

    assert file_path.read_bytes() == content


@pytest.mark.parametrize("expected_size", [2, 4])
def test_rejects_size_mismatch(
    tmp_path: Path,
    expected_size: int,
) -> None:
    content = b"abc"
    file_path = tmp_path / "source.json"
    file_path.write_bytes(content)
    metadata = replace(
        _metadata(content),
        byte_size=expected_size,
    )

    with pytest.raises(ValueError, match="byte size mismatch"):
        verify_raw_artifact(
            file_path=file_path,
            metadata=metadata,
        )


def test_rejects_changed_content_with_same_size(tmp_path: Path) -> None:
    file_path = tmp_path / "source.json"
    file_path.write_bytes(b"abd")

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_raw_artifact(
            file_path=file_path,
            metadata=_metadata(b"abc"),
        )


def test_rejects_missing_file_without_exposing_path(tmp_path: Path) -> None:
    file_path = tmp_path / "synthetic-private-path.json"

    with pytest.raises(ValueError) as captured:
        verify_raw_artifact(
            file_path=file_path,
            metadata=_metadata(b"abc"),
        )

    assert str(captured.value) == "Raw artifact could not be read."
    assert str(file_path) not in str(captured.value)


def test_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not be read"):
        verify_raw_artifact(
            file_path=tmp_path,
            metadata=_metadata(b""),
        )


@pytest.mark.parametrize(
    ("reject_code", "parser_location", "message"),
    [
        ("unsafe-code", "page[1].record[3]", "고정 코드"),
        ("ITEM_SEQ_REQUIRED", "", "비어"),
        ("ITEM_SEQ_REQUIRED", "page[1]\nrecord[3]", "형식"),
        ("ITEM_SEQ_REQUIRED", "page[1]\x7frecord[3]", "형식"),
        ("ITEM_SEQ_REQUIRED", "page[1]\x85record[3]", "형식"),
    ],
)
def test_rejects_unsafe_rejection_metadata(
    reject_code: str,
    parser_location: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        StoredRawArtifact(
            page_number=None,
            metadata=_metadata(b"synthetic rejection"),
            storage_backend="LOCAL_PRIVATE",
            object_key="sha256/synthetic-rejection.artifact",
            artifact_kind=IngestionArtifactKind.REJECTS,
            reject_code=reject_code,
            parser_location=parser_location,
        )


def test_raw_response_cannot_carry_rejection_metadata() -> None:
    with pytest.raises(ValueError, match="거부 메타데이터"):
        StoredRawArtifact(
            page_number=1,
            metadata=_metadata(b"synthetic raw response"),
            storage_backend="LOCAL_PRIVATE",
            object_key="sha256/synthetic-response.artifact",
            reject_code="ITEM_SEQ_REQUIRED",
            parser_location="page[1].record[3]",
        )


def test_typed_artifact_integrity_and_unavailable_errors(tmp_path: Path) -> None:
    from ai_worker.tasks.rag.source_ingestion.artifacts import (
        RawArtifactIntegrityError,
        RawArtifactUnavailableError,
        read_verified_raw_artifact,
    )

    # 1. Size mismatch (larger) -> RawArtifactIntegrityError
    file_path = tmp_path / "oversize.bin"
    file_path.write_bytes(b"12345")
    meta_small = replace(_metadata(b"1234"), byte_size=4)
    with pytest.raises(RawArtifactIntegrityError) as exc_info:
        read_verified_raw_artifact(file_path=file_path, metadata=meta_small)
    assert isinstance(exc_info.value, ValueError)
    assert str(exc_info.value) == "Raw artifact byte size mismatch."

    # 2. Size mismatch (smaller / EOF) -> RawArtifactIntegrityError
    meta_large = replace(_metadata(b"123456"), byte_size=6)
    with pytest.raises(RawArtifactIntegrityError) as exc_info:
        read_verified_raw_artifact(file_path=file_path, metadata=meta_large)
    assert isinstance(exc_info.value, ValueError)
    assert str(exc_info.value) == "Raw artifact byte size mismatch."

    # 3. Checksum mismatch -> RawArtifactIntegrityError
    meta_wrong_checksum = replace(_metadata(b"12345"), raw_checksum="a" * 64)
    with pytest.raises(RawArtifactIntegrityError) as exc_info:
        read_verified_raw_artifact(file_path=file_path, metadata=meta_wrong_checksum)
    assert isinstance(exc_info.value, ValueError)
    assert str(exc_info.value) == "Raw artifact checksum mismatch."

    # 4. Missing file (OSError) -> RawArtifactUnavailableError
    missing_file = tmp_path / "missing.bin"
    with pytest.raises(RawArtifactUnavailableError) as exc_info_unavail:
        read_verified_raw_artifact(file_path=missing_file, metadata=meta_small)
    assert isinstance(exc_info_unavail.value, ValueError)
    assert str(exc_info_unavail.value) == "Raw artifact could not be read."
    assert exc_info_unavail.value.__cause__ is None
    assert str(missing_file) not in str(exc_info_unavail.value)

    # 5. Directory path (OSError) -> RawArtifactUnavailableError
    with pytest.raises(RawArtifactUnavailableError) as exc_info_dir:
        read_verified_raw_artifact(file_path=tmp_path, metadata=meta_small)
    assert isinstance(exc_info_dir.value, ValueError)
    assert str(exc_info_dir.value) == "Raw artifact could not be read."
    assert exc_info_dir.value.__cause__ is None
