"""SQLAlchemy 기반 Source Snapshot lifecycle 저장소입니다."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, String, column, func, insert, select, table, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
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

_SOURCE = table(
    "rag_source",
    column("id", String(36)),
    column("source_code", String(100)),
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
    column("source_version", String(255)),
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

    async def try_lock_acquisition(self, identity: SourceOperationIdentity) -> UUID:
        """같은 Source가 수집 중이면 기다리지 않고 안전한 고정 예외를 반환합니다."""
        statement = _operation_lookup(identity).with_for_update(
            of=_SOURCE,
            skip_locked=True,
        )
        result = await self._session.execute(statement)
        operation_id = result.scalar_one_or_none()
        if operation_id is not None:
            return UUID(str(operation_id))

        existence_result = await self._session.execute(_operation_lookup(identity))
        if existence_result.scalar_one_or_none() is None:
            raise ValueError("Source operation 수집 대상을 찾을 수 없습니다.")
        raise SourceAcquisitionInProgressError("Source acquisition is already in progress.")

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
        result = await self._session.execute(
            text(
                "SELECT transition_rag_source_snapshot(:snapshot_id, :expected, :next, :verified, :effective, :actor)"
            ),
            {
                "snapshot_id": str(snapshot_id),
                "expected": expected_status.value,
                "next": new_status.value,
                "verified": verified_at,
                "effective": effective_at,
                "actor": selected_by,
            },
        )
        return result.scalar_one() is True


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
