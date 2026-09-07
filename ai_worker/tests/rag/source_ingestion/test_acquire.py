import hashlib
from pathlib import Path

import pytest

from ai_worker.tasks.rag.source_ingestion.acquire import (
    verify_raw_artifact_manifest,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum


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
