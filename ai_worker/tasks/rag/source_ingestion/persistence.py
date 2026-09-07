"""검증된 Source 원본 보관과 Snapshot 저장을 순서대로 연결합니다."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ai_worker.tasks.rag.source_ingestion.acquire import (
    preserve_raw_artifacts,
    preserve_rejection_artifact,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    RawArtifactStore,
    validate_artifact_binding,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionMetadata,
    SnapshotLifecycleRepository,
    SnapshotPersistenceResult,
    persist_product_ingestion_result,
)


@dataclass(frozen=True, slots=True)
class RejectionArtifactInput:
    file_path: Path
    metadata: RawArtifactMetadata
    reject_code: str
    parser_location: str

    def __post_init__(self) -> None:
        validate_artifact_binding(
            page_number=None,
            artifact_kind=IngestionArtifactKind.REJECTS,
            reject_code=self.reject_code,
            parser_location=self.parser_location,
        )


async def preserve_and_persist_product_ingestion_result(
    *,
    repository: SnapshotLifecycleRepository,
    artifact_store: RawArtifactStore,
    ingestion: ProductIngestionResult,
    metadata: SnapshotIngestionMetadata,
    raw_artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
    rejection_artifacts: Iterable[RejectionArtifactInput] = (),
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
    rejection_entries = tuple(rejection_artifacts)
    if metadata.rejected_record_count == 0 and rejection_entries:
        raise ValueError("거부 레코드가 없는 실행에는 REJECTS Artifact를 기록할 수 없습니다.")
    if metadata.rejected_record_count > 0 and not rejection_entries:
        raise ValueError("거부 레코드가 있는 실행에는 REJECTS Artifact가 필요합니다.")
    artifact_keys = [entry_metadata.artifact_key for _, _, entry_metadata in entries]
    artifact_keys.extend(entry.metadata.artifact_key for entry in rejection_entries)
    if len(set(artifact_keys)) != len(artifact_keys):
        raise ValueError("Artifact key는 수집 실행 안에서 중복될 수 없습니다.")

    stored_artifacts = preserve_raw_artifacts(
        artifacts=entries,
        store=artifact_store,
    )
    stored_rejections = tuple(
        preserve_rejection_artifact(
            file_path=entry.file_path,
            metadata=entry.metadata,
            reject_code=entry.reject_code,
            parser_location=entry.parser_location,
            store=artifact_store,
        )
        for entry in rejection_entries
    )
    return await persist_product_ingestion_result(
        repository=repository,
        ingestion=ingestion,
        metadata=metadata,
        artifacts=(*stored_artifacts, *stored_rejections),
    )
