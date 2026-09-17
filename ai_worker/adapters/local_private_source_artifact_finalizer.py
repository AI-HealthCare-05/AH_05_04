"""별도 Artifact owner에게 LOCAL_PRIVATE 원본 보존을 위임합니다."""

import json
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
    read_verified_raw_artifact,
    validate_artifact_binding,
    verify_raw_artifact,
)

_STORAGE_BACKEND = "LOCAL_PRIVATE"
_RESPONSE_SCHEMA = "source-artifact-finalize@1"
_MAX_RESPONSE_BYTES = 4096
_DEFAULT_TIMEOUT_SECONDS = 30.0


class ArtifactObjectKeyError(ValueError):
    """Artifact object key가 root를 벗어났거나 writer 쓰기 가능 정책을 위반함."""


class LocalPrivateSourceArtifactReader:
    """최종 Artifact mount를 변경하지 않고 checksum 대조 후 읽습니다."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("Source artifact reader root must be an absolute path.")
        self._reject_symlink_path(root)
        try:
            root_stat = root.stat()
        except OSError:
            raise ValueError("Source artifact reader root could not be inspected.") from None
        if not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError("Source artifact reader root is not a directory.")
        if root_stat.st_mode & stat.S_IWOTH:
            raise ValueError("Source artifact reader root must not be world-writable.")
        if os.access(root, os.W_OK):
            raise ValueError("Source artifact reader root must be read-only for the writer account.")
        self._root = root.resolve(strict=True)

    def read_verified(self, *, object_key: str, metadata: RawArtifactMetadata) -> bytes:
        return read_verified_raw_artifact(
            file_path=self._resolve_object_key(object_key),
            metadata=metadata,
        )

    @staticmethod
    def _reject_symlink_path(root: Path) -> None:
        for path in (root, *root.parents):
            if path.is_symlink():
                raise ValueError("Source artifact reader root cannot use symlinks.")

    def _resolve_object_key(self, object_key: str) -> Path:
        path = (self._root / object_key).resolve()
        if not path.is_relative_to(self._root):
            raise ArtifactObjectKeyError("Source artifact object key is outside reader root.")
        if os.access(path.parent, os.W_OK) or os.access(path, os.W_OK):
            raise ArtifactObjectKeyError("Final Source artifact path must be read-only for the writer account.")
        return path


FinalizerRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class FinalizingLocalPrivateSourceArtifactStore:
    """고정 명령에 원문을 stdin으로 전달하고 최종 mount는 읽기만 합니다."""

    def __init__(
        self,
        *,
        finalizer_command: Path,
        reader: LocalPrivateSourceArtifactReader,
        runner: FinalizerRunner = subprocess.run,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._validate_finalizer_command(finalizer_command)
        if timeout_seconds <= 0:
            raise ValueError("Source artifact finalizer timeout must be positive.")
        self._command = finalizer_command
        self._reader = reader
        self._runner = runner
        self._timeout_seconds = timeout_seconds
        self.finalized: list[StoredRawArtifact] = []

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
        validate_artifact_binding(
            page_number=page_number,
            artifact_kind=artifact_kind,
            reject_code=reject_code,
            parser_location=parser_location,
        )
        verify_raw_artifact(file_path=file_path, metadata=metadata)
        expected_key = self._object_key(metadata.raw_checksum)
        command = [
            str(self._command),
            "--checksum",
            metadata.raw_checksum,
            "--byte-size",
            str(metadata.byte_size),
            "--content-type",
            metadata.content_type,
        ]
        try:
            with file_path.open("rb") as source:
                completed = self._runner(
                    command,
                    stdin=source,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env={},
                    timeout=self._timeout_seconds,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Source artifact finalizer did not complete.") from None
        if completed.returncode != 0 or len(completed.stdout) > _MAX_RESPONSE_BYTES:
            raise ValueError("Source artifact finalizer rejected the object.")
        response = self._parse_response(completed.stdout)
        if response["object_key"] != expected_key:
            raise ValueError("Source artifact finalizer returned an unexpected object key.")

        stored = StoredRawArtifact(
            page_number=page_number,
            metadata=metadata,
            storage_backend=_STORAGE_BACKEND,
            object_key=expected_key,
            artifact_kind=artifact_kind,
            reject_code=reject_code,
            parser_location=parser_location,
        )
        self.finalized.append(stored)
        self._reader.read_verified(object_key=expected_key, metadata=metadata)
        return stored

    @staticmethod
    def _validate_finalizer_command(command: Path) -> None:
        if not command.is_absolute() or any(path.is_symlink() for path in (command, *command.parents)):
            raise ValueError("Source artifact finalizer command must be an absolute non-symlink path.")
        try:
            command_stat = command.stat()
        except OSError:
            raise ValueError("Source artifact finalizer command could not be inspected.") from None
        if not stat.S_ISREG(command_stat.st_mode) or command_stat.st_uid == os.geteuid():
            raise ValueError("Source artifact finalizer command must be owned outside the writer account.")
        if command_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("Source artifact finalizer command must not be group- or world-writable.")
        if any(os.access(parent, os.W_OK) for parent in command.parents):
            raise ValueError("Source artifact finalizer command path must not be writer-writable.")
        if not os.access(command, os.X_OK):
            raise ValueError("Source artifact finalizer command is not executable.")

    @staticmethod
    def _parse_response(raw: bytes) -> dict[str, str]:
        try:
            decoded: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Source artifact finalizer returned an invalid response.") from None
        if not isinstance(decoded, dict) or set(decoded) != {"schema", "storage_backend", "object_key"}:
            raise ValueError("Source artifact finalizer returned an invalid response.")
        if decoded["schema"] != _RESPONSE_SCHEMA or decoded["storage_backend"] != _STORAGE_BACKEND:
            raise ValueError("Source artifact finalizer returned an invalid response.")
        if not isinstance(decoded["object_key"], str):
            raise ValueError("Source artifact finalizer returned an invalid response.")
        return decoded

    @staticmethod
    def _object_key(checksum: str) -> str:
        return f"sha256/{checksum[:2]}/{checksum}.artifact"
