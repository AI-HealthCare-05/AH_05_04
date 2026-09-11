"""원본 Artifact의 무결성 검증에 필요한 메타데이터입니다."""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from ai_worker.tasks.rag.source_ingestion.reject_codes import validate_parser_location

_SAFE_REJECT_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}")


class IngestionArtifactKind(StrEnum):
    RAW_RESPONSE = "RAW_RESPONSE"
    REJECTS = "REJECTS"


@dataclass(frozen=True, slots=True)
class RawArtifactMetadata:
    artifact_key: str
    raw_checksum: str
    byte_size: int
    content_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_key, str) or not self.artifact_key.strip():
            raise ValueError("Artifact key must not be empty.")
        if len(self.artifact_key) > 500:
            raise ValueError("Artifact key must not exceed 500 characters.")

        if not isinstance(self.raw_checksum, str) or not re.fullmatch(
            r"[0-9a-f]{64}",
            self.raw_checksum,
        ):
            raise ValueError("Artifact checksum must be lowercase SHA-256.")

        if type(self.byte_size) is not int or self.byte_size < 0:
            raise ValueError("Artifact byte size must be a non-negative integer.")

        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValueError("Artifact content type must not be empty.")
        if len(self.content_type) > 255:
            raise ValueError("Artifact content type must not exceed 255 characters.")


@dataclass(frozen=True, slots=True)
class StoredRawArtifact:
    """접근 통제 저장소에 보존된 원본 Artifact의 불변 참조입니다."""

    page_number: int | None
    metadata: RawArtifactMetadata
    storage_backend: str
    object_key: str
    artifact_kind: IngestionArtifactKind = IngestionArtifactKind.RAW_RESPONSE
    reject_code: str | None = None
    parser_location: str | None = None

    def __post_init__(self) -> None:
        validate_artifact_binding(
            page_number=self.page_number,
            artifact_kind=self.artifact_kind,
            reject_code=self.reject_code,
            parser_location=self.parser_location,
        )
        if not self.storage_backend.strip():
            raise ValueError("Artifact storage_backend는 비어 있을 수 없습니다.")
        if not self.object_key.strip():
            raise ValueError("Artifact object_key는 비어 있을 수 없습니다.")
        if len(self.storage_backend) > 50:
            raise ValueError("Artifact storage_backend는 50자를 초과할 수 없습니다.")
        if len(self.object_key) > 500:
            raise ValueError("Artifact object_key는 500자를 초과할 수 없습니다.")


def validate_artifact_binding(
    *,
    page_number: int | None,
    artifact_kind: IngestionArtifactKind,
    reject_code: str | None,
    parser_location: str | None,
) -> None:
    if not isinstance(artifact_kind, IngestionArtifactKind):
        raise ValueError("Artifact kind가 올바르지 않습니다.")
    if artifact_kind is IngestionArtifactKind.RAW_RESPONSE:
        _validate_raw_response_binding(page_number, reject_code, parser_location)
        return
    _validate_rejection_binding(page_number, reject_code, parser_location)


def _validate_raw_response_binding(
    page_number: int | None,
    reject_code: str | None,
    parser_location: str | None,
) -> None:
    if type(page_number) is not int or page_number < 1:
        raise ValueError("RAW_RESPONSE page_number는 1 이상의 정수여야 합니다.")
    if reject_code is not None or parser_location is not None:
        raise ValueError("RAW_RESPONSE에는 거부 메타데이터를 기록할 수 없습니다.")


def _validate_rejection_binding(
    page_number: int | None,
    reject_code: str | None,
    parser_location: str | None,
) -> None:
    if page_number is not None:
        raise ValueError("REJECTS는 page_number 대신 parser_location을 사용합니다.")
    if reject_code is None or not _SAFE_REJECT_CODE_PATTERN.fullmatch(reject_code):
        raise ValueError("REJECTS reject_code는 안전한 고정 코드여야 합니다.")
    if parser_location is None or not parser_location.strip():
        raise ValueError("REJECTS parser_location은 비어 있을 수 없습니다.")
    try:
        validate_parser_location(parser_location)
    except ValueError:
        raise ValueError("REJECTS parser_location 형식이 올바르지 않습니다.") from None


class RawArtifactStore(Protocol):
    """검증하면서 원본을 불변 저장소에 보존하는 포트입니다."""

    def put_verified(
        self,
        *,
        page_number: int | None,
        file_path: Path,
        metadata: RawArtifactMetadata,
        artifact_kind: IngestionArtifactKind = IngestionArtifactKind.RAW_RESPONSE,
        reject_code: str | None = None,
        parser_location: str | None = None,
    ) -> StoredRawArtifact: ...


def verify_raw_artifact(
    *,
    file_path: Path,
    metadata: RawArtifactMetadata,
) -> None:
    """원본 파일의 실제 크기와 SHA-256이 메타데이터와 일치하는지 확인합니다."""
    _read_verified_raw_artifact(
        file_path=file_path,
        metadata=metadata,
        retain_content=False,
    )


def read_verified_raw_artifact(
    *,
    file_path: Path,
    metadata: RawArtifactMetadata,
) -> bytes:
    """크기와 SHA-256을 확인한 동일 원본 바이트를 반환합니다."""
    return _read_verified_raw_artifact(
        file_path=file_path,
        metadata=metadata,
        retain_content=True,
    )


def _read_verified_raw_artifact(
    *,
    file_path: Path,
    metadata: RawArtifactMetadata,
    retain_content: bool,
) -> bytes:
    digest = hashlib.sha256()
    bytes_read = 0
    chunk_size = 1024 * 1024
    chunks: list[bytes] = []

    try:
        with file_path.open("rb") as file_stream:
            while True:
                # 예상 크기를 초과하는지 확인할 1바이트까지만 읽습니다.
                read_size = min(
                    chunk_size,
                    metadata.byte_size - bytes_read + 1,
                )
                chunk = file_stream.read(read_size)

                if not chunk:
                    break

                bytes_read += len(chunk)

                if bytes_read > metadata.byte_size:
                    raise ValueError("Raw artifact byte size mismatch.")

                digest.update(chunk)
                if retain_content:
                    chunks.append(chunk)
    except OSError:
        # 예외 메시지에 파일 경로나 원문을 포함하지 않습니다.
        raise ValueError("Raw artifact could not be read.") from None

    if bytes_read != metadata.byte_size:
        raise ValueError("Raw artifact byte size mismatch.")

    if digest.hexdigest() != metadata.raw_checksum:
        raise ValueError("Raw artifact checksum mismatch.")

    return b"".join(chunks)
