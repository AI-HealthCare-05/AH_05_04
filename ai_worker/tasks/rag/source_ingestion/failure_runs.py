"""Snapshot 후보 생성 전 Source ingestion 실패 이력을 저장합니다."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.source_client.contracts import (
    SourceOperationIdentity,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    StoredRawArtifact,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotRunRecord


class IngestionProcessingFailureCode(StrEnum):
    """Parser 이후 단계에서 기록할 수 있는 안전한 고정 실패 코드입니다."""

    PARSER_VALIDATION_FAILED = "PARSER_VALIDATION_FAILED"
    REJECTION_LIMIT_EXCEEDED = "REJECTION_LIMIT_EXCEEDED"


@dataclass(frozen=True, slots=True)
class FailedIngestionRunMetadata:
    """Snapshot 메타데이터가 생기기 전 실패 실행의 최소 이력입니다."""

    run_group_key: str
    attempt_number: int
    started_at: datetime
    finished_at: datetime
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.run_group_key.strip() or len(self.run_group_key) > 100:
            raise ValueError("run_group_key는 비어 있지 않은 100자 이하여야 합니다.")
        if self.attempt_number < 1:
            raise ValueError("attempt_number는 1 이상이어야 합니다.")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms는 0 이상이어야 합니다.")
        for timestamp in (self.started_at, self.finished_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("실패 실행 시각은 timezone-aware 값이어야 합니다.")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at은 started_at보다 빠를 수 없습니다.")


@dataclass(frozen=True, slots=True)
class FailedIngestionRunResult:
    operation_id: UUID
    ingestion_run_id: UUID
    failure_code: str


class FailedRunRepository(Protocol):
    async def lock_operation(self, identity: SourceOperationIdentity) -> UUID: ...

    async def create_run(self, record: SnapshotRunRecord) -> UUID: ...

    async def create_artifacts(
        self,
        *,
        ingestion_run_id: UUID,
        artifacts: tuple[StoredRawArtifact, ...],
    ) -> None: ...


async def record_source_run_failure(
    *,
    repository: FailedRunRepository,
    result: SourceRunResult,
    metadata: FailedIngestionRunMetadata,
) -> FailedIngestionRunResult:
    """Provider 수집 실패를 부분 page나 Snapshot 없이 기록합니다."""
    if result.status is SourceRunStatus.SUCCEEDED or result.failure is None:
        raise ValueError("성공 Source run은 실패 이력으로 기록할 수 없습니다.")
    if result.pages:
        raise ValueError("실패 Source run은 부분 page를 노출할 수 없습니다.")

    return await _record_failed_run(
        repository=repository,
        identity=result.operation,
        metadata=metadata,
        failure_code=result.failure.code.value,
        artifacts=(),
    )


async def record_processing_failure(
    *,
    repository: FailedRunRepository,
    identity: SourceOperationIdentity,
    metadata: FailedIngestionRunMetadata,
    failure_code: IngestionProcessingFailureCode,
    artifacts: tuple[StoredRawArtifact, ...],
) -> FailedIngestionRunResult:
    """Parser 또는 거부 한도 실패와 이미 보존한 원본 참조를 기록합니다."""
    if type(failure_code) is not IngestionProcessingFailureCode:
        raise ValueError("허용된 ingestion processing failure code가 아닙니다.")
    _validate_failure_artifacts(failure_code=failure_code, artifacts=artifacts)
    return await _record_failed_run(
        repository=repository,
        identity=identity,
        metadata=metadata,
        failure_code=failure_code.value,
        artifacts=artifacts,
    )


async def _record_failed_run(
    *,
    repository: FailedRunRepository,
    identity: SourceOperationIdentity,
    metadata: FailedIngestionRunMetadata,
    failure_code: str,
    artifacts: tuple[StoredRawArtifact, ...],
) -> FailedIngestionRunResult:
    operation_id = await repository.lock_operation(identity)
    ingestion_run_id = await repository.create_run(
        SnapshotRunRecord(
            operation_id=operation_id,
            snapshot_id=None,
            run_group_key=metadata.run_group_key,
            attempt_number=metadata.attempt_number,
            run_status="FAILED",
            started_at=metadata.started_at,
            finished_at=metadata.finished_at,
            duration_ms=metadata.duration_ms,
            failure_code=failure_code,
        )
    )
    if artifacts:
        await repository.create_artifacts(
            ingestion_run_id=ingestion_run_id,
            artifacts=artifacts,
        )
    return FailedIngestionRunResult(operation_id, ingestion_run_id, failure_code)


def _validate_failure_artifacts(
    *,
    failure_code: IngestionProcessingFailureCode,
    artifacts: tuple[StoredRawArtifact, ...],
) -> None:
    artifact_keys = [artifact.metadata.artifact_key for artifact in artifacts]
    if len(set(artifact_keys)) != len(artifact_keys):
        raise ValueError("Artifact key는 실패 실행 안에서 중복될 수 없습니다.")

    raw_pages = [
        artifact.page_number for artifact in artifacts if artifact.artifact_kind is IngestionArtifactKind.RAW_RESPONSE
    ]
    if len(set(raw_pages)) != len(raw_pages):
        raise ValueError("RAW_RESPONSE page_number는 실패 실행 안에서 중복될 수 없습니다.")

    if failure_code is IngestionProcessingFailureCode.REJECTION_LIMIT_EXCEEDED and not any(
        artifact.artifact_kind is IngestionArtifactKind.REJECTS for artifact in artifacts
    ):
        raise ValueError("거부 한도 실패에는 REJECTS Artifact가 필요합니다.")
