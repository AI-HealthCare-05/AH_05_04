import hashlib
import stat
from pathlib import Path

import pytest

from ai_worker.adapters.local_private_source_artifact_store import (
    LocalPrivateSourceArtifactStore,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata


def _metadata(content: bytes, *, artifact_key: str = "page-0001.json") -> RawArtifactMetadata:
    return RawArtifactMetadata(
        artifact_key=artifact_key,
        raw_checksum=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/json",
    )


def test_preserves_verified_content_with_private_permissions(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    storage_root = tmp_path / "private"
    content = b'{"synthetic":true}'
    source.write_bytes(content)
    metadata = _metadata(content)
    store = LocalPrivateSourceArtifactStore(storage_root)

    stored = store.put_verified(page_number=1, file_path=source, metadata=metadata)

    destination = storage_root / stored.object_key
    assert stored.storage_backend == "LOCAL_PRIVATE"
    assert stored.object_key == f"sha256/{metadata.raw_checksum[:2]}/{metadata.raw_checksum}.artifact"
    assert destination.read_bytes() == content
    assert stat.S_IMODE(storage_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert list(destination.parent.glob(".pending-*")) == []


def test_same_verified_content_reuses_immutable_object(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    content = b'{"synthetic":true}'
    source.write_bytes(content)
    metadata = _metadata(content)
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")

    first = store.put_verified(page_number=1, file_path=source, metadata=metadata)
    second = store.put_verified(page_number=2, file_path=source, metadata=metadata)

    assert first.object_key == second.object_key
    assert first.page_number == 1
    assert second.page_number == 2


def test_rejects_changed_source_without_publishing_partial_object(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    expected = b'{"synthetic":true}'
    source.write_bytes(b'{"synthetic":false}')
    metadata = _metadata(expected)
    storage_root = tmp_path / "private"
    store = LocalPrivateSourceArtifactStore(storage_root)

    with pytest.raises(ValueError, match="byte size mismatch|checksum mismatch"):
        store.put_verified(page_number=1, file_path=source, metadata=metadata)

    assert list(storage_root.rglob("*.artifact")) == []
    assert list(storage_root.rglob(".pending-*")) == []


def test_rejects_tampered_existing_object(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    content = b'{"synthetic":true}'
    source.write_bytes(content)
    metadata = _metadata(content)
    storage_root = tmp_path / "private"
    store = LocalPrivateSourceArtifactStore(storage_root)
    stored = store.put_verified(page_number=1, file_path=source, metadata=metadata)
    (storage_root / stored.object_key).write_bytes(b"x" * len(content))

    with pytest.raises(ValueError, match="checksum mismatch"):
        store.put_verified(page_number=1, file_path=source, metadata=metadata)
