"""검증된 Source 수집 결과를 Snapshot 이력에 연결하는 계약입니다."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult

SOURCE_VERSION_CONFLICT = "SOURCE_VERSION_CONFLICT"
_SAFE_FAILURE_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}")


class SnapshotIngestionDecision(StrEnum):
    """검증된 수집 결과와 기존 Snapshot을 비교한 결과입니다."""

    CREATED = "CREATED"
    NO_CHANGE = "NO_CHANGE"
    SOURCE_VERSION_CONFLICT = SOURCE_VERSION_CONFLICT


class SnapshotVerificationStatus(StrEnum):
    """#291 Snapshot 검증 상태 계약입니다."""

    PENDING = "PENDING"
    CURRENT = "CURRENT"
    STALE = "STALE"
    FAILED = "FAILED"


class SnapshotSelectionDecision(StrEnum):
    """검증된 Snapshot의 현재성 선택 결과입니다."""

    ACTIVATED = "ACTIVATED"
    RESTORED = "RESTORED"
    ALREADY_CURRENT = "ALREADY_CURRENT"


@dataclass(frozen=True, slots=True)
class SnapshotReference:
    """수집 결과 비교에 필요한 기존 Snapshot의 최소 정보입니다."""

    snapshot_id: UUID
    source_version: str
    canonical_checksum: str
    schema_version: str
    parser_version: str
    normalization_version: str
    canonicalization_spec_version: str


@dataclass(frozen=True, slots=True)
class SnapshotIngestionMetadata:
    """검증 결과 외에 Source 수집 실행 계층이 선택하는 저장 메타데이터입니다."""

    source_version: str
    schema_version: str
    parser_version: str
    normalization_version: str
    rejected_record_count: int
    run_group_key: str
    attempt_number: int
    started_at: datetime
    finished_at: datetime
    collected_at: datetime
    duration_ms: int | None = None
    verified_by: str | None = None

    def __post_init__(self) -> None:
        required_text = (
            self.source_version,
            self.schema_version,
            self.parser_version,
            self.normalization_version,
            self.run_group_key,
        )
        if any(not value.strip() for value in required_text):
            raise ValueError("Snapshot 저장 version과 run group은 비어 있을 수 없습니다.")
        if self.rejected_record_count < 0:
            raise ValueError("rejected_record_count는 0 이상이어야 합니다.")
        if self.attempt_number < 1:
            raise ValueError("attempt_number는 1 이상이어야 합니다.")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms는 0 이상이어야 합니다.")
        for timestamp in (self.started_at, self.finished_at, self.collected_at):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Snapshot 저장 시각은 timezone-aware 값이어야 합니다.")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at은 started_at보다 빠를 수 없습니다.")


@dataclass(frozen=True, slots=True)
class SnapshotCreateRequest:
    operation_id: UUID
    ingestion: ProductIngestionResult
    metadata: SnapshotIngestionMetadata
    supersedes_snapshot_id: UUID | None


@dataclass(frozen=True, slots=True)
class SnapshotRunRecord:
    operation_id: UUID
    snapshot_id: UUID | None
    run_group_key: str
    attempt_number: int
    run_status: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int | None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class StoredRawArtifact:
    """접근 통제 저장소에 보존된 원본 Artifact의 불변 참조입니다."""

    page_number: int
    metadata: RawArtifactMetadata
    storage_backend: str
    object_key: str

    def __post_init__(self) -> None:
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("Artifact page_number는 1 이상의 정수여야 합니다.")
        if not self.storage_backend.strip():
            raise ValueError("Artifact storage_backend는 비어 있을 수 없습니다.")
        if not self.object_key.strip():
            raise ValueError("Artifact object_key는 비어 있을 수 없습니다.")
        if len(self.storage_backend) > 50:
            raise ValueError("Artifact storage_backend는 50자를 초과할 수 없습니다.")
        if len(self.object_key) > 500:
            raise ValueError("Artifact object_key는 500자를 초과할 수 없습니다.")
        if len(self.metadata.artifact_key) > 500:
            raise ValueError("Artifact key는 500자를 초과할 수 없습니다.")
        if len(self.metadata.content_type) > 255:
            raise ValueError("Artifact content_type은 255자를 초과할 수 없습니다.")


@dataclass(frozen=True, slots=True)
class SnapshotPersistenceResult:
    decision: SnapshotIngestionDecision
    operation_id: UUID
    ingestion_run_id: UUID
    snapshot_id: UUID | None


@dataclass(frozen=True, slots=True)
class SnapshotStatusReference:
    snapshot_id: UUID
    operation_id: UUID
    verification_status: SnapshotVerificationStatus


@dataclass(frozen=True, slots=True)
class SnapshotSelectionResult:
    decision: SnapshotSelectionDecision
    operation_id: UUID
    snapshot_id: UUID
    replaced_snapshot_id: UUID | None


class SnapshotLifecycleRepository(Protocol):
    """한 DB transaction 안에서 Snapshot lifecycle을 저장하는 포트입니다."""

    async def lock_operation(self, identity: SourceOperationIdentity) -> UUID:
        """Operation을 조회해 잠그고, 없으면 ValueError를 발생시킵니다."""
        ...

    async def get_snapshot_by_version(
        self,
        *,
        operation_id: UUID,
        source_version: str,
    ) -> SnapshotReference | None: ...

    async def get_latest_snapshot(self, *, operation_id: UUID) -> SnapshotReference | None: ...

    async def create_snapshot(self, request: SnapshotCreateRequest) -> UUID: ...

    async def append_verification(
        self,
        *,
        snapshot_id: UUID,
        check_name: str,
        result: str,
        verified_at: datetime,
        verified_by: str | None,
        details_summary: str | None = None,
    ) -> None: ...

    async def create_run(self, record: SnapshotRunRecord) -> UUID: ...

    async def create_artifacts(
        self,
        *,
        ingestion_run_id: UUID,
        artifacts: tuple[StoredRawArtifact, ...],
    ) -> None: ...

    async def lock_snapshot_operation(self, *, snapshot_id: UUID) -> UUID:
        """Snapshot의 Operation을 잠그고, 없으면 ValueError를 발생시킵니다."""
        ...

    async def get_snapshot_status(
        self,
        *,
        operation_id: UUID,
        snapshot_id: UUID,
    ) -> SnapshotStatusReference | None: ...

    async def get_current_snapshot_status(self, *, operation_id: UUID) -> SnapshotStatusReference | None: ...

    async def change_snapshot_status(
        self,
        *,
        snapshot_id: UUID,
        expected_status: SnapshotVerificationStatus,
        new_status: SnapshotVerificationStatus,
        verified_at: datetime | None = None,
        effective_at: datetime | None = None,
    ) -> bool: ...


def decide_snapshot_ingestion(
    *,
    ingestion: ProductIngestionResult,
    metadata: SnapshotIngestionMetadata,
    same_version: SnapshotReference | None,
    latest: SnapshotReference | None,
) -> tuple[SnapshotIngestionDecision, SnapshotReference | None]:
    """동일 version 충돌을 우선 차단하고 직전 내용과 변화 여부를 판단합니다."""
    if same_version is not None:
        if not _has_same_canonical_contract(same_version, ingestion=ingestion, metadata=metadata):
            return SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT, same_version
        return SnapshotIngestionDecision.NO_CHANGE, same_version

    if latest is not None and _has_same_canonical_contract(latest, ingestion=ingestion, metadata=metadata):
        return SnapshotIngestionDecision.NO_CHANGE, latest

    return SnapshotIngestionDecision.CREATED, latest


async def persist_product_ingestion_result(
    *,
    repository: SnapshotLifecycleRepository,
    ingestion: ProductIngestionResult,
    metadata: SnapshotIngestionMetadata,
    artifacts: tuple[StoredRawArtifact, ...],
) -> SnapshotPersistenceResult:
    """검증된 결과를 현재 transaction에 기록하며 commit은 호출자가 담당합니다."""
    if metadata.rejected_record_count > ingestion.record_count:
        raise ValueError("rejected_record_count는 record_count를 초과할 수 없습니다.")
    _validate_ingestion_artifacts(ingestion=ingestion, artifacts=artifacts)

    operation_id = await repository.lock_operation(ingestion.identity)
    same_version = await repository.get_snapshot_by_version(
        operation_id=operation_id,
        source_version=metadata.source_version,
    )
    latest = await repository.get_latest_snapshot(operation_id=operation_id)
    decision, comparison_snapshot = decide_snapshot_ingestion(
        ingestion=ingestion,
        metadata=metadata,
        same_version=same_version,
        latest=latest,
    )

    if decision is SnapshotIngestionDecision.CREATED:
        snapshot_id = await repository.create_snapshot(
            SnapshotCreateRequest(
                operation_id=operation_id,
                ingestion=ingestion,
                metadata=metadata,
                supersedes_snapshot_id=(comparison_snapshot.snapshot_id if comparison_snapshot is not None else None),
            )
        )
        await repository.append_verification(
            snapshot_id=snapshot_id,
            check_name="source-ingestion-integrity",
            result="PASSED",
            verified_at=metadata.finished_at,
            verified_by=metadata.verified_by,
        )
        run_status = "SUCCEEDED_WITH_REJECTIONS" if metadata.rejected_record_count else "SUCCEEDED"
        ingestion_run_id = await repository.create_run(
            _run_record(
                operation_id=operation_id,
                snapshot_id=snapshot_id,
                metadata=metadata,
                run_status=run_status,
            )
        )
        await repository.create_artifacts(ingestion_run_id=ingestion_run_id, artifacts=artifacts)
        return SnapshotPersistenceResult(decision, operation_id, ingestion_run_id, snapshot_id)

    if comparison_snapshot is None:
        raise RuntimeError("Snapshot 비교 결과가 없습니다.")

    if decision is SnapshotIngestionDecision.NO_CHANGE:
        await repository.append_verification(
            snapshot_id=comparison_snapshot.snapshot_id,
            check_name="source-ingestion-integrity",
            result="NO_CHANGE",
            verified_at=metadata.finished_at,
            verified_by=metadata.verified_by,
        )
        ingestion_run_id = await repository.create_run(
            _run_record(
                operation_id=operation_id,
                snapshot_id=comparison_snapshot.snapshot_id,
                metadata=metadata,
                run_status="NO_CHANGE",
            )
        )
        await repository.create_artifacts(ingestion_run_id=ingestion_run_id, artifacts=artifacts)
        return SnapshotPersistenceResult(
            decision,
            operation_id,
            ingestion_run_id,
            comparison_snapshot.snapshot_id,
        )

    ingestion_run_id = await repository.create_run(
        _run_record(
            operation_id=operation_id,
            snapshot_id=None,
            metadata=metadata,
            run_status="FAILED",
            failure_code=SOURCE_VERSION_CONFLICT,
        )
    )
    await repository.create_artifacts(ingestion_run_id=ingestion_run_id, artifacts=artifacts)
    return SnapshotPersistenceResult(decision, operation_id, ingestion_run_id, None)


def _validate_ingestion_artifacts(
    *,
    ingestion: ProductIngestionResult,
    artifacts: tuple[StoredRawArtifact, ...],
) -> None:
    if len(artifacts) != ingestion.artifact_count:
        raise ValueError("Artifact 개수가 검증된 수집 결과와 일치하지 않습니다.")
    page_numbers = {artifact.page_number for artifact in artifacts}
    if len(page_numbers) != len(artifacts):
        raise ValueError("Artifact page_number는 수집 실행 안에서 중복될 수 없습니다.")
    manifest_checksum = raw_manifest_checksum(artifact.metadata for artifact in artifacts)
    if manifest_checksum != ingestion.raw_manifest_checksum:
        raise ValueError("Artifact manifest checksum이 검증된 수집 결과와 일치하지 않습니다.")


async def select_current_snapshot(
    *,
    repository: SnapshotLifecycleRepository,
    snapshot_id: UUID,
    selected_at: datetime,
    selected_by: str | None,
) -> SnapshotSelectionResult:
    """검증된 Snapshot을 CURRENT로 선택하며 Runtime Bundle은 변경하지 않습니다."""
    if selected_at.tzinfo is None or selected_at.utcoffset() is None:
        raise ValueError("Snapshot 선택 시각은 timezone-aware 값이어야 합니다.")

    operation_id = await repository.lock_snapshot_operation(snapshot_id=snapshot_id)
    target = await repository.get_snapshot_status(operation_id=operation_id, snapshot_id=snapshot_id)
    if target is None:
        raise ValueError("선택할 Snapshot을 찾을 수 없습니다.")
    if target.verification_status is SnapshotVerificationStatus.FAILED:
        raise ValueError("FAILED Snapshot은 CURRENT로 선택할 수 없습니다.")
    if target.verification_status is SnapshotVerificationStatus.CURRENT:
        return SnapshotSelectionResult(
            decision=SnapshotSelectionDecision.ALREADY_CURRENT,
            operation_id=operation_id,
            snapshot_id=snapshot_id,
            replaced_snapshot_id=None,
        )

    current = await repository.get_current_snapshot_status(operation_id=operation_id)
    if current is not None:
        changed = await repository.change_snapshot_status(
            snapshot_id=current.snapshot_id,
            expected_status=SnapshotVerificationStatus.CURRENT,
            new_status=SnapshotVerificationStatus.STALE,
        )
        if not changed:
            raise RuntimeError("기존 CURRENT Snapshot 상태가 변경되었습니다.")

    previous_status = target.verification_status
    changed = await repository.change_snapshot_status(
        snapshot_id=target.snapshot_id,
        expected_status=previous_status,
        new_status=SnapshotVerificationStatus.CURRENT,
        verified_at=selected_at if previous_status is SnapshotVerificationStatus.PENDING else None,
        effective_at=selected_at,
    )
    if not changed:
        raise RuntimeError("선택 대상 Snapshot 상태가 변경되었습니다.")

    await repository.append_verification(
        snapshot_id=target.snapshot_id,
        check_name="snapshot-current-selection",
        result="PASSED",
        verified_at=selected_at,
        verified_by=selected_by,
    )
    decision = (
        SnapshotSelectionDecision.ACTIVATED
        if previous_status is SnapshotVerificationStatus.PENDING
        else SnapshotSelectionDecision.RESTORED
    )
    return SnapshotSelectionResult(
        decision=decision,
        operation_id=operation_id,
        snapshot_id=target.snapshot_id,
        replaced_snapshot_id=current.snapshot_id if current is not None else None,
    )


async def fail_snapshot_verification(
    *,
    repository: SnapshotLifecycleRepository,
    snapshot_id: UUID,
    failure_code: str,
    failed_at: datetime,
    verified_by: str | None,
) -> SnapshotStatusReference:
    """PENDING Snapshot을 안전한 고정 code로 실패 처리합니다."""
    if _SAFE_FAILURE_CODE_PATTERN.fullmatch(failure_code) is None:
        raise ValueError("Snapshot failure_code 형식이 올바르지 않습니다.")
    if failed_at.tzinfo is None or failed_at.utcoffset() is None:
        raise ValueError("Snapshot 실패 시각은 timezone-aware 값이어야 합니다.")

    operation_id = await repository.lock_snapshot_operation(snapshot_id=snapshot_id)
    target = await repository.get_snapshot_status(operation_id=operation_id, snapshot_id=snapshot_id)
    if target is None:
        raise ValueError("실패 처리할 Snapshot을 찾을 수 없습니다.")
    if target.verification_status is not SnapshotVerificationStatus.PENDING:
        raise ValueError("PENDING Snapshot만 실패 처리할 수 있습니다.")

    changed = await repository.change_snapshot_status(
        snapshot_id=snapshot_id,
        expected_status=SnapshotVerificationStatus.PENDING,
        new_status=SnapshotVerificationStatus.FAILED,
        verified_at=failed_at,
    )
    if not changed:
        raise RuntimeError("실패 처리 대상 Snapshot 상태가 변경되었습니다.")
    await repository.append_verification(
        snapshot_id=snapshot_id,
        check_name="snapshot-verification",
        result="FAILED",
        verified_at=failed_at,
        verified_by=verified_by,
        details_summary=failure_code,
    )
    return SnapshotStatusReference(
        snapshot_id=snapshot_id,
        operation_id=operation_id,
        verification_status=SnapshotVerificationStatus.FAILED,
    )


def _run_record(
    *,
    operation_id: UUID,
    snapshot_id: UUID | None,
    metadata: SnapshotIngestionMetadata,
    run_status: str,
    failure_code: str | None = None,
) -> SnapshotRunRecord:
    return SnapshotRunRecord(
        operation_id=operation_id,
        snapshot_id=snapshot_id,
        run_group_key=metadata.run_group_key,
        attempt_number=metadata.attempt_number,
        run_status=run_status,
        started_at=metadata.started_at,
        finished_at=metadata.finished_at,
        duration_ms=metadata.duration_ms,
        failure_code=failure_code,
    )


def _has_same_canonical_contract(
    snapshot: SnapshotReference,
    *,
    ingestion: ProductIngestionResult,
    metadata: SnapshotIngestionMetadata,
) -> bool:
    return (
        snapshot.canonical_checksum == ingestion.canonical_checksum
        and snapshot.schema_version == metadata.schema_version
        and snapshot.parser_version == metadata.parser_version
        and snapshot.normalization_version == metadata.normalization_version
        and snapshot.canonicalization_spec_version == ingestion.canonicalization_spec_version
    )
