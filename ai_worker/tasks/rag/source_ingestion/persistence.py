"""검증된 Source 원본 보관과 Snapshot 저장을 순서대로 연결합니다."""

from collections.abc import Iterable
from pathlib import Path

from ai_worker.tasks.rag.source_ingestion.acquire import preserve_raw_artifacts
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    RawArtifactStore,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionMetadata,
    SnapshotLifecycleRepository,
    SnapshotPersistenceResult,
    persist_product_ingestion_result,
)


async def preserve_and_persist_product_ingestion_result(
    *,
    repository: SnapshotLifecycleRepository,
    artifact_store: RawArtifactStore,
    ingestion: ProductIngestionResult,
    metadata: SnapshotIngestionMetadata,
    raw_artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
) -> SnapshotPersistenceResult:
    """검증 결과와 같은 원본만 불변 보관한 뒤 DB transaction에 연결합니다."""
    entries = tuple(raw_artifacts)
    if len(entries) != ingestion.artifact_count:
        raise ValueError("Artifact 개수가 검증된 수집 결과와 일치하지 않습니다.")
    page_numbers = {page_number for page_number, _, _ in entries}
    if len(page_numbers) != len(entries):
        raise ValueError("Artifact page_number는 수집 실행 안에서 중복될 수 없습니다.")
    manifest_checksum = raw_manifest_checksum(metadata for _, _, metadata in entries)
    if manifest_checksum != ingestion.raw_manifest_checksum:
        raise ValueError("Artifact manifest checksum이 검증된 수집 결과와 일치하지 않습니다.")

    stored_artifacts = preserve_raw_artifacts(
        artifacts=entries,
        store=artifact_store,
    )
    return await persist_product_ingestion_result(
        repository=repository,
        ingestion=ingestion,
        metadata=metadata,
        artifacts=stored_artifacts,
    )
