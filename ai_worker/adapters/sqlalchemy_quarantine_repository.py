"""메시지 격리와 DLQ Outbox를 저장하는 SQLAlchemy Repository입니다."""

from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    column,
    select,
    table,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core.quarantine import (
    QuarantineReceipt,
    QuarantineRequest,
)

_AI_JOB = table(
    "ai_job",
    column("id", String(36)),
)

_MESSAGE_QUARANTINE = table(
    "message_quarantine",
    column("id", String(36)),
    column("stream_name", String(100)),
    column("stream_entry_id", String(100)),
    column("message_digest", String(128)),
    column("job_id", String(36)),
    column("original_event_id", String(36)),
    column("failure_code", String(100)),
    column("original_schema_version", String(20)),
    column("trace_id", String(100)),
    column("received_at", DateTime(timezone=True)),
)

_DLQ_OUTBOX_EVENT = table(
    "dlq_outbox_event",
    column("event_id", String(36)),
    column("quarantine_id", String(36)),
    column("event_kind", String(30)),
    column("schema_version", String(20)),
    column("original_schema_version", String(20)),
    column("status", String(20)),
    column("attempt_count", Integer),
    column("available_at", DateTime(timezone=True)),
)


class QuarantineStateError(RuntimeError):
    """격리 상태를 안전하게 저장하거나 복원할 수 없는 경우입니다."""

    def __init__(self) -> None:
        super().__init__("메시지 격리 상태 저장에 실패했습니다.")


class SqlAlchemyQuarantineRepository:
    """격리 row와 DLQ Outbox를 같은 transaction에 저장합니다.

    commit과 rollback은 호출자가 담당합니다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        request: QuarantineRequest,
    ) -> QuarantineReceipt:
        safe_job_id = await self._resolve_existing_job_id(request.job_id)
        quarantine_id = await self._insert_or_get_quarantine(
            request,
            safe_job_id=safe_job_id,
        )
        dlq_event_id = await self._insert_or_get_dlq_event(
            request,
            quarantine_id=quarantine_id,
        )

        return QuarantineReceipt(
            quarantine_id=quarantine_id,
            dlq_event_id=dlq_event_id,
        )

    async def _resolve_existing_job_id(
        self,
        job_id: UUID | None,
    ) -> str | None:
        if job_id is None:
            return None

        statement = select(_AI_JOB.c.id).where(_AI_JOB.c.id == str(job_id))
        result = await self._session.execute(statement)
        existing_job_id = result.scalar_one_or_none()

        if existing_job_id is None:
            return None

        return str(existing_job_id)

    async def _insert_or_get_quarantine(
        self,
        request: QuarantineRequest,
        *,
        safe_job_id: str | None,
    ) -> UUID:
        new_quarantine_id = uuid4()

        statement = (
            insert(_MESSAGE_QUARANTINE)
            .values(
                id=str(new_quarantine_id),
                stream_name=request.stream_name,
                stream_entry_id=request.stream_entry_id,
                message_digest=request.message_digest,
                job_id=safe_job_id,
                original_event_id=(str(request.original_event_id) if request.original_event_id is not None else None),
                failure_code=request.failure_code.value,
                original_schema_version=request.original_schema_version,
                trace_id=request.trace_id,
                received_at=request.received_at,
            )
            .on_conflict_do_nothing(
                index_elements=(
                    _MESSAGE_QUARANTINE.c.stream_name,
                    _MESSAGE_QUARANTINE.c.stream_entry_id,
                )
            )
            .returning(_MESSAGE_QUARANTINE.c.id)
        )

        result = await self._session.execute(statement)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            return UUID(str(inserted_id))

        existing_statement = select(_MESSAGE_QUARANTINE.c.id).where(
            _MESSAGE_QUARANTINE.c.stream_name == request.stream_name,
            _MESSAGE_QUARANTINE.c.stream_entry_id == request.stream_entry_id,
        )
        existing_result = await self._session.execute(existing_statement)
        existing_id = existing_result.scalar_one_or_none()

        if existing_id is None:
            raise QuarantineStateError()

        return UUID(str(existing_id))

    async def _insert_or_get_dlq_event(
        self,
        request: QuarantineRequest,
        *,
        quarantine_id: UUID,
    ) -> UUID:
        new_event_id = uuid4()

        statement = (
            insert(_DLQ_OUTBOX_EVENT)
            .values(
                event_id=str(new_event_id),
                quarantine_id=str(quarantine_id),
                event_kind="QUARANTINE_RECORDED",
                schema_version="1.0",
                original_schema_version=request.original_schema_version,
                status="PENDING",
                attempt_count=0,
                available_at=request.received_at,
            )
            .on_conflict_do_nothing(index_elements=(_DLQ_OUTBOX_EVENT.c.quarantine_id,))
            .returning(_DLQ_OUTBOX_EVENT.c.event_id)
        )

        result = await self._session.execute(statement)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            return UUID(str(inserted_id))

        existing_statement = select(_DLQ_OUTBOX_EVENT.c.event_id).where(
            _DLQ_OUTBOX_EVENT.c.quarantine_id == str(quarantine_id)
        )
        existing_result = await self._session.execute(existing_statement)
        existing_id = existing_result.scalar_one_or_none()

        if existing_id is None:
            raise QuarantineStateError()

        return UUID(str(existing_id))
