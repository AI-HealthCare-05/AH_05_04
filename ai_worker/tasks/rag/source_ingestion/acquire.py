"""수집한 원본 Artifact 목록의 무결성을 검증합니다."""

from collections.abc import Iterable
from pathlib import Path

from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    verify_raw_artifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum


def verify_raw_artifact_manifest(
    artifacts: Iterable[tuple[Path, RawArtifactMetadata]],
) -> str:
    """전달된 모든 파일이 검증된 경우에만 manifest checksum을 반환합니다."""
    entries = tuple(artifacts)

    # 빈 목록과 중복 Artifact Key를 파일 읽기 전에 거부합니다.
    manifest_checksum = raw_manifest_checksum(metadata for _, metadata in entries)

    for file_path, metadata in entries:
        verify_raw_artifact(
            file_path=file_path,
            metadata=metadata,
        )

    return manifest_checksum
