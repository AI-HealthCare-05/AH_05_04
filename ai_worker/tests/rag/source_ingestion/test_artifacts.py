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
        ("MISSING_ITEM_SEQ", "", "비어"),
        ("MISSING_ITEM_SEQ", "page[1]\nrecord[3]", "형식"),
        ("MISSING_ITEM_SEQ", "page[1]\x7frecord[3]", "형식"),
        ("MISSING_ITEM_SEQ", "page[1]\x85record[3]", "형식"),
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
            reject_code="MISSING_ITEM_SEQ",
            parser_location="page[1].record[3]",
        )
