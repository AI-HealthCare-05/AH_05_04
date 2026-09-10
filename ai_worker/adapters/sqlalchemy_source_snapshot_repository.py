"""SQLAlchemy 기반 Source Snapshot lifecycle 저장소입니다."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, Numeric, String, column, func, insert, select, table, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from ai_worker.tasks.rag.source_client.contracts import EmptyResultPolicy, SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.artifacts import StoredRawArtifact
from ai_worker.tasks.rag.source_ingestion.service import SourceAcquisitionInProgressError
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SNAPSHOT_PUBLICATION_APPROVAL_CHECK,
    SnapshotCreateRequest,
    SnapshotLifecycleRepository,
    SnapshotReference,
    SnapshotRunRecord,
    SnapshotStatusReference,
    SnapshotVerificationStatus,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_policy import SourceSnapshotPolicy

_SOURCE = table(
    "rag_source",
    column("id", String(36)),
    column("source_code", String(100)),
    column("max_rejected_records", Integer),
    column("max_rejection_rate", Numeric()),
    column("empty_result_policy", String(20)),
)
_ENDPOINT = table(
    "rag_source_endpoint",
    column("id", String(36)),
    column("source_id", String(36)),
    column("endpoint_code", String(100)),
)
_OPERATION = table(
    "rag_source_operation",
    column("id", String(36)),
    column("endpoint_id", String(36)),
    column("operation_code", String(100)),
)
_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("source_version", String(200)),
    column("external_version", String(200)),
    column("raw_manifest_checksum", String(64)),
    column("canonical_checksum", String(64)),
    column("schema_version", String(100)),
    column("parser_version", String(100)),
    column("normalization_version", String(100)),
    column("canonicalization_spec_version", String(100)),
    column("endpoint_receipt_hash", String(64)),
    column("record_count", Integer),
    column("rejected_record_count", Integer),
    column("verification_status", String(20)),
    column("verification_seal_id", String(36)),
    column("collected_at", DateTime(timezone=True)),
    column("verified_at", DateTime(timezone=True)),
    column("effective_at", DateTime(timezone=True)),
    column("supersedes_snapshot_id", String(36)),
    column("created_at", DateTime(timezone=True)),
)
_INGESTION_RUN = table(
    "rag_source_ingestion_run",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("run_group_key", String(100)),
    column("snapshot_id", String(36)),
    column("run_status", String(40)),
    column("attempt_number", Integer),
    column("failure_code", String(100)),
    column("failure_message", String(255)),
    column("duration_ms", Integer),
    column("started_at", DateTime(timezone=True)),
    column("finished_at", DateTime(timezone=True)),
)
_INGESTION_ARTIFACT = table(
    "rag_source_ingestion_artifact",
    column("id", String(36)),
    column("ingestion_run_id", String(36)),
    column("page_number", Integer),
    column("artifact_kind", String(20)),
    column("artifact_key", String(500)),
    column("storage_backend", String(50)),
    column("object_key", String(500)),
    column("raw_checksum", String(64)),
    column("byte_size", Integer),
    column("content_type", String(255)),
    column("reject_code", String(100)),
    column("parser_location", String(255)),
)
_VERIFICATION = table(
    "rag_source_snapshot_verification",
    column("id", String(36)),
    column("snapshot_id", String(36)),
    column("check_name", String(100)),
    column("verification_result", String(20)),
    column("details_summary", String(255)),
    column("verified_by", String(100)),
    column("verified_at", DateTime(timezone=True)),
)


class SqlAlchemySourceSnapshotRepository(SnapshotLifecycleRepository):
    """#291 테이블에 기록하며 commit과 rollback은 호출자가 담당합니다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_operation(self, identity: SourceOperationIdentity) -> UUID:
        statement = _operation_lookup(identity).with_for_update(of=_OPERATION)
        result = await self._session.execute(statement)
        operation_id = result.scalar_one_or_none()
        if operation_id is None:
            raise ValueError("Source operation 저장 대상을 찾을 수 없습니다.")
        return UUID(str(operation_id))

    async def get_source_policy(self, *, operation_id: UUID) -> SourceSnapshotPolicy:
        row = (
            await self._session.execute(
                select(_SOURCE.c.max_rejected_records, _SOURCE.c.max_rejection_rate, _SOURCE.c.empty_result_policy)
                .select_from(
                    _SOURCE.join(_ENDPOINT, _ENDPOINT.c.source_id == _SOURCE.c.id).join(
                        _OPERATION, _OPERATION.c.endpoint_id == _ENDPOINT.c.id
                    )
                )
                .where(_OPERATION.c.id == str(operation_id))
            )
        ).one()
        return SourceSnapshotPolicy(
            row.max_rejected_records, row.max_rejection_rate, EmptyResultPolicy(row.empty_result_policy)
        )

    async def try_lock_acquisition(self, identity: SourceOperationIdentity) -> UUID:
        """같은 Source가 수집 중이면 기다리지 않고 안전한 고정 예외를 반환합니다."""
        result = await self._session.execute(_operation_lookup(identity).add_columns(_SOURCE.c.id))
        row = result.one_or_none()
        if row is None:
            raise ValueError("Source operation 수집 대상을 찾을 수 없습니다.")
        operation_id, source_id = row
        # PostgreSQL's built-in transaction lock requires no UPDATE privilege on Source.
        # The namespace + Source ID is shared across operations and released on rollback.
        acquired = await self._session.scalar(
            select(func.pg_try_advisory_xact_lock(func.hashtextextended(f"source-acquisition:{source_id}", 0)))
        )
        if not acquired:
            raise SourceAcquisitionInProgressError("Source acquisition is already in progress.")
        return UUID(str(operation_id))

    async def get_snapshot_by_version(
        self,
        *,
        operation_id: UUID,
        source_version: str,
    ) -> SnapshotReference | None:
        statement = (
            select(
                _SNAPSHOT.c.id,
                _SNAPSHOT.c.source_version,
                _SNAPSHOT.c.canonical_checksum,
                _SNAPSHOT.c.schema_version,
                _SNAPSHOT.c.parser_version,
                _SNAPSHOT.c.normalization_version,
                _SNAPSHOT.c.canonicalization_spec_version,
                _SNAPSHOT.c.endpoint_receipt_hash,
                _SNAPSHOT.c.rejected_record_count,
                _SNAPSHOT.c.verification_status,
            )
            .where(
                _SNAPSHOT.c.operation_id == str(operation_id),
                _SNAPSHOT.c.source_version == source_version,
            )
            .order_by(
                (_SNAPSHOT.c.verification_status == SnapshotVerificationStatus.FAILED).asc(),
                _SNAPSHOT.c.collected_at.desc(),
                _SNAPSHOT.c.created_at.desc(),
                _SNAPSHOT.c.id.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.mappings().one_or_none()
        return _snapshot_reference(row)

    async def get_latest_snapshot(self, *, operation_id: UUID) -> SnapshotReference | None:
        statement = (
            select(
                _SNAPSHOT.c.id,
                _SNAPSHOT.c.source_version,
                _SNAPSHOT.c.canonical_checksum,
                _SNAPSHOT.c.schema_version,
                _SNAPSHOT.c.parser_version,
                _SNAPSHOT.c.normalization_version,
                _SNAPSHOT.c.canonicalization_spec_version,
                _SNAPSHOT.c.endpoint_receipt_hash,
                _SNAPSHOT.c.rejected_record_count,
                _SNAPSHOT.c.verification_status,
            )
            .where(
                _SNAPSHOT.c.operation_id == str(operation_id),
                _SNAPSHOT.c.verification_status != SnapshotVerificationStatus.FAILED,
            )
            .order_by(
                _SNAPSHOT.c.collected_at.desc(),
                _SNAPSHOT.c.created_at.desc(),
                _SNAPSHOT.c.id.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.mappings().one_or_none()
        return _snapshot_reference(row)

    async def create_snapshot(self, request: SnapshotCreateRequest) -> UUID:
        snapshot_id = uuid4()
        await self._session.execute(
            insert(_SNAPSHOT).values(
                id=str(snapshot_id),
                operation_id=str(request.operation_id),
                source_version=request.metadata.source_version,
                external_version=request.metadata.external_version,
                raw_manifest_checksum=request.ingestion.raw_manifest_checksum,
                canonical_checksum=request.ingestion.canonical_checksum,
                schema_version=request.metadata.schema_version,
                parser_version=request.metadata.parser_version,
                normalization_version=request.metadata.normalization_version,
                canonicalization_spec_version=request.ingestion.canonicalization_spec_version,
                endpoint_receipt_hash=request.ingestion.endpoint_receipt_hash,
                record_count=request.ingestion.record_count,
                rejected_record_count=request.metadata.rejected_record_count,
                verification_status="PENDING",
                collected_at=request.metadata.collected_at,
                verified_at=None,
                effective_at=None,
                supersedes_snapshot_id=(
                    str(request.supersedes_snapshot_id) if request.supersedes_snapshot_id is not None else None
                ),
            )
        )
        return snapshot_id

    async def append_verification(
        self,
        *,
        snapshot_id: UUID,
        check_name: str,
        result: str,
        verified_at: datetime,
        verified_by: str | None,
        details_summary: str | None = None,
    ) -> None:
        await self.lock_snapshot_operation(snapshot_id=snapshot_id)
        await self._session.execute(
            insert(_VERIFICATION).values(
                id=str(uuid4()),
                snapshot_id=str(snapshot_id),
                check_name=check_name,
                verification_result=result,
                details_summary=details_summary,
                verified_by=verified_by,
                verified_at=verified_at,
            )
        )

    async def create_run(self, record: SnapshotRunRecord) -> UUID:
        ingestion_run_id = uuid4()
        await self._session.execute(
            insert(_INGESTION_RUN).values(
                id=str(ingestion_run_id),
                operation_id=str(record.operation_id),
                run_group_key=record.run_group_key,
                snapshot_id=str(record.snapshot_id) if record.snapshot_id is not None else None,
                run_status=record.run_status,
                attempt_number=record.attempt_number,
                failure_code=record.failure_code,
                failure_message=None,
                duration_ms=record.duration_ms,
                started_at=record.started_at,
                finished_at=record.finished_at,
            )
        )
        return ingestion_run_id

    async def create_artifacts(
        self,
        *,
        ingestion_run_id: UUID,
        artifacts: tuple[StoredRawArtifact, ...],
    ) -> None:
        values = [
            {
                "id": str(uuid4()),
                "ingestion_run_id": str(ingestion_run_id),
                "page_number": artifact.page_number,
                "artifact_kind": artifact.artifact_kind,
                "artifact_key": artifact.metadata.artifact_key,
                "storage_backend": artifact.storage_backend,
                "object_key": artifact.object_key,
                "raw_checksum": artifact.metadata.raw_checksum,
                "byte_size": artifact.metadata.byte_size,
                "content_type": artifact.metadata.content_type,
                "reject_code": artifact.reject_code,
                "parser_location": artifact.parser_location,
            }
            for artifact in artifacts
        ]
        await self._session.execute(insert(_INGESTION_ARTIFACT), values)

    async def lock_snapshot_operation(self, *, snapshot_id: UUID) -> UUID:
        statement = (
            select(_OPERATION.c.id)
            .select_from(_OPERATION.join(_SNAPSHOT, _SNAPSHOT.c.operation_id == _OPERATION.c.id))
            .where(_SNAPSHOT.c.id == str(snapshot_id))
            .with_for_update(of=_OPERATION)
        )
        result = await self._session.execute(statement)
        operation_id = result.scalar_one_or_none()
        if operation_id is None:
            raise ValueError("Snapshot의 Source operation을 찾을 수 없습니다.")
        return UUID(str(operation_id))

    async def get_snapshot_status(
        self,
        *,
        operation_id: UUID,
        snapshot_id: UUID,
    ) -> SnapshotStatusReference | None:
        statement = select(
            _SNAPSHOT.c.id,
            _SNAPSHOT.c.operation_id,
            _SNAPSHOT.c.verification_status,
            _SNAPSHOT.c.rejected_record_count,
        ).where(
            _SNAPSHOT.c.id == str(snapshot_id),
            _SNAPSHOT.c.operation_id == str(operation_id),
        )
        result = await self._session.execute(statement)
        return _snapshot_status_reference(result.mappings().one_or_none())

    async def get_current_snapshot_status(self, *, operation_id: UUID) -> SnapshotStatusReference | None:
        statement = select(
            _SNAPSHOT.c.id,
            _SNAPSHOT.c.operation_id,
            _SNAPSHOT.c.verification_status,
            _SNAPSHOT.c.rejected_record_count,
        ).where(
            _SNAPSHOT.c.operation_id == str(operation_id),
            _SNAPSHOT.c.verification_status == SnapshotVerificationStatus.CURRENT,
        )
        result = await self._session.execute(statement)
        return _snapshot_status_reference(result.mappings().one_or_none())

    async def has_passed_verification(
        self,
        *,
        snapshot_id: UUID,
        check_name: str,
    ) -> bool:
        statement = (
            select(_VERIFICATION.c.id)
            .where(
                _VERIFICATION.c.snapshot_id == str(snapshot_id),
                _VERIFICATION.c.check_name == check_name,
                _VERIFICATION.c.verification_result == "PASSED",
            )
            .limit(1)
        )
        if check_name == SNAPSHOT_PUBLICATION_APPROVAL_CHECK:
            statement = statement.where(
                _VERIFICATION.c.verified_by.is_not(None),
                func.length(func.trim(_VERIFICATION.c.verified_by)) > 0,
            )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def change_snapshot_status(
        self,
        *,
        snapshot_id: UUID,
        expected_status: SnapshotVerificationStatus,
        new_status: SnapshotVerificationStatus,
        verified_at: datetime | None = None,
        effective_at: datetime | None = None,
        selected_by: str | None = None,
    ) -> bool:
        # 감사 실패를 호출자가 잡더라도 상태 변경만 남지 않도록 savepoint로 묶습니다.
        # 외부 transaction의 commit은 호출자가 담당합니다.
        async with self._session.begin_nested():
            target = await self._lock_transition_target(snapshot_id)
            if target is None or target["verification_status"] != expected_status:
                return False
            _validate_snapshot_transition(expected_status, new_status, verified_at, effective_at, selected_by)
            if (
                new_status is SnapshotVerificationStatus.CURRENT
                and target["rejected_record_count"] > 0
                and not await self.has_passed_verification(
                    snapshot_id=snapshot_id, check_name=SNAPSHOT_PUBLICATION_APPROVAL_CHECK
                )
            ):
                raise ValueError("Snapshot publication approval required")
            seal_id = target["verification_seal_id"]
            if seal_id is None:
                seal_id = str(uuid4())
                assert verified_at is not None
                await self._session.execute(
                    insert(_VERIFICATION).values(
                        id=seal_id,
                        snapshot_id=str(snapshot_id),
                        check_name="snapshot-state-seal",
                        verification_result="NO_CHANGE",
                        details_summary="Python transition: immutable state anchor; not publication approval",
                        verified_by=selected_by,
                        verified_at=verified_at,
                    )
                )
            values: dict[str, Any] = {"verification_status": new_status.value, "verification_seal_id": seal_id}
            if verified_at is not None:
                values["verified_at"] = verified_at
            if effective_at is not None:
                values["effective_at"] = effective_at
            result = await self._session.execute(
                update(_SNAPSHOT)
                .where(
                    _SNAPSHOT.c.id == str(snapshot_id),
                    _SNAPSHOT.c.verification_status == expected_status.value,
                )
                .values(**values)
                .returning(_SNAPSHOT.c.id)
            )
            if result.scalar_one_or_none() is None:
                return False
            if new_status is SnapshotVerificationStatus.CURRENT:
                assert effective_at is not None
                await self.append_verification(
                    snapshot_id=snapshot_id,
                    check_name="snapshot-current-selection",
                    result="PASSED",
                    verified_at=effective_at,
                    verified_by=selected_by,
                    details_summary="Python transaction: snapshot selection",
                )
            return True

    async def _lock_transition_target(self, snapshot_id: UUID) -> RowMapping | None:
        operation = await self._session.execute(
            select(_OPERATION.c.id)
            .select_from(_OPERATION.join(_SNAPSHOT, _SNAPSHOT.c.operation_id == _OPERATION.c.id))
            .where(_SNAPSHOT.c.id == str(snapshot_id))
            .with_for_update(of=_OPERATION)
        )
        operation_id = operation.scalar_one_or_none()
        if operation_id is None:
            return None
        target = await self._session.execute(
            select(_SNAPSHOT.c.verification_status, _SNAPSHOT.c.rejected_record_count, _SNAPSHOT.c.verification_seal_id)
            .where(_SNAPSHOT.c.id == str(snapshot_id), _SNAPSHOT.c.operation_id == operation_id)
            .with_for_update(of=_SNAPSHOT)
        )
        return target.mappings().one_or_none()


def _validate_snapshot_transition(
    expected: SnapshotVerificationStatus,
    next_status: SnapshotVerificationStatus,
    verified_at: datetime | None,
    effective_at: datetime | None,
    selected_by: str | None,
) -> None:
    allowed = {
        (SnapshotVerificationStatus.PENDING, SnapshotVerificationStatus.CURRENT),
        (SnapshotVerificationStatus.PENDING, SnapshotVerificationStatus.FAILED),
        (SnapshotVerificationStatus.CURRENT, SnapshotVerificationStatus.STALE),
        (SnapshotVerificationStatus.STALE, SnapshotVerificationStatus.CURRENT),
    }
    if (expected, next_status) not in allowed:
        raise ValueError("Invalid Snapshot transition")
    if (expected is SnapshotVerificationStatus.PENDING) != (verified_at is not None):
        raise ValueError("Invalid Snapshot verification timestamp")
    if (next_status is SnapshotVerificationStatus.CURRENT) != (effective_at is not None):
        raise ValueError("Invalid Snapshot selection timestamp")
    for timestamp in (verified_at, effective_at):
        if timestamp is not None and (timestamp.tzinfo is None or timestamp.utcoffset() is None):
            raise ValueError("Snapshot timestamp must be timezone-aware")
    if next_status is SnapshotVerificationStatus.CURRENT:
        if selected_by is None or not selected_by.strip() or len(selected_by) > 100:
            raise ValueError("Snapshot selection requires an actor of at most 100 characters")


def _snapshot_reference(row: RowMapping | None) -> SnapshotReference | None:
    if row is None:
        return None
    return SnapshotReference(
        snapshot_id=UUID(str(row["id"])),
        source_version=str(row["source_version"]),
        canonical_checksum=str(row["canonical_checksum"]),
        schema_version=str(row["schema_version"]),
        parser_version=str(row["parser_version"]),
        normalization_version=str(row["normalization_version"]),
        canonicalization_spec_version=str(row["canonicalization_spec_version"]),
        endpoint_receipt_hash=(str(row["endpoint_receipt_hash"]) if row["endpoint_receipt_hash"] is not None else None),
        rejected_record_count=int(row["rejected_record_count"]),
        verification_status=SnapshotVerificationStatus(str(row["verification_status"])),
    )


def _snapshot_status_reference(row: RowMapping | None) -> SnapshotStatusReference | None:
    if row is None:
        return None
    return SnapshotStatusReference(
        snapshot_id=UUID(str(row["id"])),
        operation_id=UUID(str(row["operation_id"])),
        verification_status=SnapshotVerificationStatus(str(row["verification_status"])),
        rejected_record_count=int(row["rejected_record_count"]),
    )


def _operation_lookup(identity: SourceOperationIdentity) -> Select[tuple[Any]]:
    return (
        select(_OPERATION.c.id)
        .select_from(
            _OPERATION.join(_ENDPOINT, _OPERATION.c.endpoint_id == _ENDPOINT.c.id).join(
                _SOURCE,
                _ENDPOINT.c.source_id == _SOURCE.c.id,
            )
        )
        .where(
            _SOURCE.c.source_code == identity.source_code,
            _ENDPOINT.c.endpoint_code == identity.endpoint_code,
            _OPERATION.c.operation_code == identity.operation_code,
        )
    )
