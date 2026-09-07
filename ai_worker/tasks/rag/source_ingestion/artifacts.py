"""원본 Artifact의 무결성 검증에 필요한 메타데이터입니다."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RawArtifactMetadata:
    artifact_key: str
    raw_checksum: str
    byte_size: int
    content_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_key, str) or not self.artifact_key.strip():
            raise ValueError("Artifact key must not be empty.")

        if not isinstance(self.raw_checksum, str) or not re.fullmatch(
            r"[0-9a-f]{64}",
            self.raw_checksum,
        ):
            raise ValueError("Artifact checksum must be lowercase SHA-256.")

        if type(self.byte_size) is not int or self.byte_size < 0:
            raise ValueError("Artifact byte size must be a non-negative integer.")

        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValueError("Artifact content type must not be empty.")


def verify_raw_artifact(
    *,
    file_path: Path,
    metadata: RawArtifactMetadata,
) -> None:
    """원본 파일의 실제 크기와 SHA-256이 메타데이터와 일치하는지 확인합니다."""
    digest = hashlib.sha256()
    bytes_read = 0
    chunk_size = 1024 * 1024

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
    except OSError:
        # 예외 메시지에 파일 경로나 원문을 포함하지 않습니다.
        raise ValueError("Raw artifact could not be read.") from None

    if bytes_read != metadata.byte_size:
        raise ValueError("Raw artifact byte size mismatch.")

    if digest.hexdigest() != metadata.raw_checksum:
        raise ValueError("Raw artifact checksum mismatch.")
