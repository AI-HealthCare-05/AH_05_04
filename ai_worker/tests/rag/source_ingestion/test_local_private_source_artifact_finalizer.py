import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from ai_worker.adapters import local_private_source_artifact_finalizer as adapter_module
from ai_worker.adapters.local_private_source_artifact_finalizer import (
    FinalizingLocalPrivateSourceArtifactStore,
    LocalPrivateSourceArtifactReader,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata


def _metadata(content: bytes) -> RawArtifactMetadata:
    return RawArtifactMetadata(
        artifact_key="synthetic/EE.xml",
        raw_checksum=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/xml",
    )


def _command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    command = tmp_path / "source591-preserve"
    command.write_text("synthetic")
    command.chmod(0o500)
    writer_euid = os.geteuid()
    real_access = os.access
    monkeypatch.setattr(adapter_module.os, "geteuid", lambda: writer_euid + 1)
    monkeypatch.setattr(
        adapter_module.os,
        "access",
        lambda path, mode: False if Path(path) in command.parents and mode == os.W_OK else real_access(path, mode),
    )
    return command


def test_writer_delegates_finalization_and_only_reads_final_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"<synthetic>true</synthetic>"
    source = tmp_path / "EE.xml"
    source.write_bytes(content)
    metadata = _metadata(content)
    final_root = tmp_path / "final-read-only"
    final_root.mkdir(mode=0o500)
    object_key = f"sha256/{metadata.raw_checksum[:2]}/{metadata.raw_checksum}.artifact"
    calls: list[dict[str, object]] = []

    def finalizer(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append({"command": command, **kwargs})
        final_root.chmod(0o700)
        destination = final_root / object_key
        destination.parent.mkdir(mode=0o700, parents=True)
        destination.write_bytes(cast(BinaryIO, kwargs["stdin"]).read())
        destination.chmod(0o400)
        destination.parent.chmod(0o500)
        final_root.chmod(0o500)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "schema": "source-artifact-finalize@1",
                    "storage_backend": "LOCAL_PRIVATE",
                    "object_key": object_key,
                }
            ).encode(),
            b"",
        )

    store = FinalizingLocalPrivateSourceArtifactStore(
        finalizer_command=_command(tmp_path, monkeypatch),
        reader=LocalPrivateSourceArtifactReader(final_root),
        runner=finalizer,
    )
    stored = store.put_verified(page_number=1, file_path=source, metadata=metadata)

    assert stored.object_key == object_key
    assert calls[0]["env"] == {}
    assert calls[0]["stderr"] is subprocess.DEVNULL
    assert "--checksum" in calls[0]["command"]  # type: ignore[operator]
    assert not hasattr(store, "delete")


def test_writer_rejects_untrusted_finalizer_command(tmp_path: Path) -> None:
    command = tmp_path / "writer-owned-command"
    command.write_text("synthetic")
    command.chmod(0o500)
    final_root = tmp_path / "final"
    final_root.mkdir(mode=0o500)

    with pytest.raises(ValueError, match="owned outside"):
        FinalizingLocalPrivateSourceArtifactStore(
            finalizer_command=command,
            reader=LocalPrivateSourceArtifactReader(final_root),
        )


def test_writer_rejects_unexpected_finalizer_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"synthetic"
    source = tmp_path / "EE.xml"
    source.write_bytes(content)
    final_root = tmp_path / "final"
    final_root.mkdir(mode=0o500)

    def finalizer(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, b'{"object_key":"wrong"}', b"")

    store = FinalizingLocalPrivateSourceArtifactStore(
        finalizer_command=_command(tmp_path, monkeypatch),
        reader=LocalPrivateSourceArtifactReader(final_root),
        runner=finalizer,
    )
    with pytest.raises(ValueError, match="invalid response"):
        store.put_verified(page_number=1, file_path=source, metadata=_metadata(content))


def test_finalized_object_is_tracked_when_read_only_requery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"synthetic"
    source = tmp_path / "EE.xml"
    source.write_bytes(content)
    metadata = _metadata(content)
    expected_key = f"sha256/{metadata.raw_checksum[:2]}/{metadata.raw_checksum}.artifact"
    final_root = tmp_path / "final"
    final_root.mkdir(mode=0o500)

    def finalizer(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "schema": "source-artifact-finalize@1",
                    "storage_backend": "LOCAL_PRIVATE",
                    "object_key": expected_key,
                }
            ).encode(),
            b"",
        )

    store = FinalizingLocalPrivateSourceArtifactStore(
        finalizer_command=_command(tmp_path, monkeypatch),
        reader=LocalPrivateSourceArtifactReader(final_root),
        runner=finalizer,
    )
    with pytest.raises(ValueError, match="could not be read"):
        store.put_verified(page_number=1, file_path=source, metadata=metadata)

    assert [stored.object_key for stored in store.finalized] == [expected_key]


def test_reader_rejects_world_writable_or_symlink_root(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    with pytest.raises(ValueError, match="world-writable"):
        LocalPrivateSourceArtifactReader(shared)

    writable = tmp_path / "writer-writable"
    writable.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="read-only"):
        LocalPrivateSourceArtifactReader(writable)

    private = tmp_path / "private"
    private.mkdir(mode=0o500)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks") as captured:
        LocalPrivateSourceArtifactReader(link)
    from ai_worker.adapters.local_private_source_artifact_finalizer import ArtifactObjectKeyError

    assert not isinstance(captured.value, ArtifactObjectKeyError)


def test_reader_object_key_and_path_security_errors(tmp_path: Path) -> None:
    from ai_worker.adapters.local_private_source_artifact_finalizer import (
        ArtifactObjectKeyError,
        LocalPrivateSourceArtifactReader,
    )

    final_root = tmp_path / "final-read-only"
    final_root.mkdir(mode=0o500)
    reader = LocalPrivateSourceArtifactReader(final_root)
    metadata = _metadata(b"synthetic")

    # 1. Root escape -> ArtifactObjectKeyError
    with pytest.raises(ArtifactObjectKeyError) as exc_info:
        reader.read_verified(object_key="../escape.artifact", metadata=metadata)
    assert isinstance(exc_info.value, ValueError)
    assert str(exc_info.value) == "Source artifact object key is outside reader root."

    # 2. Writer-writable parent or file -> ArtifactObjectKeyError
    # Create an artifact inside final_root but leave it or its parent writer-writable
    final_root.chmod(0o700)
    sub = final_root / "writable_sub"
    sub.mkdir(mode=0o700)
    art = sub / "test.artifact"
    art.write_bytes(b"synthetic")
    art.chmod(0o600)
    final_root.chmod(0o500)

    with pytest.raises(ArtifactObjectKeyError) as exc_info:
        reader.read_verified(object_key="writable_sub/test.artifact", metadata=metadata)
    assert isinstance(exc_info.value, ValueError)
    assert str(exc_info.value) == "Final Source artifact path must be read-only for the writer account."
