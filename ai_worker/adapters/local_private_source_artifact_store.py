"""로컬 접근 통제 디렉터리에 Source 원본을 불변 보존합니다."""

import hashlib
import os
import tempfile
from pathlib import Path

from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    StoredRawArtifact,
    verify_raw_artifact,
)

_STORAGE_BACKEND = "LOCAL_PRIVATE"
_CHUNK_SIZE = 1024 * 1024


class LocalPrivateSourceArtifactStore:
    """SHA-256 기반 object key로 원본을 원자적·멱등하게 보존합니다."""

    def __init__(self, root: Path) -> None:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root = root.resolve()
        if not self._root.is_dir():
            raise ValueError("Source artifact storage root is not a directory.")
        os.chmod(self._root, 0o700)

    def put_verified(
        self,
        *,
        page_number: int,
        file_path: Path,
        metadata: RawArtifactMetadata,
    ) -> StoredRawArtifact:
        object_key = self._object_key(metadata.raw_checksum)
        destination = self._resolve_object_key(object_key)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(destination.parent, 0o700)

        if destination.exists():
            verify_raw_artifact(file_path=file_path, metadata=metadata)
            verify_raw_artifact(file_path=destination, metadata=metadata)
            return self._reference(page_number, metadata, object_key)

        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".pending-",
                dir=destination.parent,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            self._copy_verified(
                descriptor=descriptor,
                source_path=file_path,
                metadata=metadata,
            )
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                verify_raw_artifact(file_path=destination, metadata=metadata)
            self._sync_directory(destination.parent)
        except OSError:
            raise ValueError("Raw artifact could not be preserved.") from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return self._reference(page_number, metadata, object_key)

    @staticmethod
    def _object_key(raw_checksum: str) -> str:
        return f"sha256/{raw_checksum[:2]}/{raw_checksum}.artifact"

    def _resolve_object_key(self, object_key: str) -> Path:
        destination = (self._root / object_key).resolve()
        if not destination.is_relative_to(self._root):
            raise ValueError("Source artifact object key is outside storage root.")
        return destination

    @staticmethod
    def _copy_verified(
        *,
        descriptor: int,
        source_path: Path,
        metadata: RawArtifactMetadata,
    ) -> None:
        digest = hashlib.sha256()
        bytes_read = 0
        try:
            with os.fdopen(descriptor, "wb") as destination, source_path.open("rb") as source:
                while True:
                    read_size = min(_CHUNK_SIZE, metadata.byte_size - bytes_read + 1)
                    chunk = source.read(read_size)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    if bytes_read > metadata.byte_size:
                        raise ValueError("Raw artifact byte size mismatch.")
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        except OSError:
            raise ValueError("Raw artifact could not be read or preserved.") from None

        if bytes_read != metadata.byte_size:
            raise ValueError("Raw artifact byte size mismatch.")
        if digest.hexdigest() != metadata.raw_checksum:
            raise ValueError("Raw artifact checksum mismatch.")

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _reference(
        page_number: int,
        metadata: RawArtifactMetadata,
        object_key: str,
    ) -> StoredRawArtifact:
        return StoredRawArtifact(
            page_number=page_number,
            metadata=metadata,
            storage_backend=_STORAGE_BACKEND,
            object_key=object_key,
        )
