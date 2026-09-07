import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
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
