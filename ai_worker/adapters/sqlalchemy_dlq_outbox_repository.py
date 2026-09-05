"""DLQ Outbox 선점·완료·재예약 SQLAlchemy Repository입니다."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    and_,
    column,
    or_,
    select,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core.dlq import ClaimedDlqEvent
from ai_worker.core.quarantine import (
    DeadLetterEnvelope,
    QuarantineFailureCode,
)

_MESSAGE_QUARANTINE = table(
    "message_quarantine",
    column("id", String(36)),
    column("stream_entry_id", String(100)),
    column("message_digest", String(128)),
    column("failure_code", String(100)),
    column("trace_id", String(100)),
)

_DLQ_OUTBOX_EVENT = table(
    "dlq_outbox_event",
    column("event_id", String(36)),
    column("quarantine_id", String(36)),
    column("original_schema_version", String(20)),
    column("status", String(20)),
    column("attempt_count", Integer),
    column("available_at", DateTime(timezone=True)),
    column("claim_token", String(100)),
    column("claim_expires_at", DateTime(timezone=True)),
    column("last_error_code", String(100)),
    column("published_at", DateTime(timezone=True)),
    column("updated_at", DateTime(timezone=True)),
)


class DlqOutboxStateError(RuntimeError):
    """DLQ Outbox claim 소유권이 더 이상 유효하지 않습니다."""

    def __init__(self) -> None:
        super().__init__("DLQ Outbox 상태를 안전하게 변경할 수 없습니다.")


class SqlAlchemyDlqOutboxRepository:
    """DLQ Outbox row를 claim token fencing으로 변경합니다.

    commit과 rollback은 호출자가 담당합니다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_next(
        self,
        *,
        now: datetime,
        claim_expires_at: datetime,
    ) -> ClaimedDlqEvent | None:
        """발행 가능하거나 claim이 만료된 row 하나를 선점합니다."""

        candidate_statement = (
            select(
                _DLQ_OUTBOX_EVENT.c.event_id,
                _DLQ_OUTBOX_EVENT.c.quarantine_id,
                _DLQ_OUTBOX_EVENT.c.attempt_count,
                _DLQ_OUTBOX_EVENT.c.original_schema_version,
                _MESSAGE_QUARANTINE.c.stream_entry_id,
                _MESSAGE_QUARANTINE.c.message_digest,
                _MESSAGE_QUARANTINE.c.failure_code,
                _MESSAGE_QUARANTINE.c.trace_id,
            )
            .select_from(
                _DLQ_OUTBOX_EVENT.join(
                    _MESSAGE_QUARANTINE,
                    _MESSAGE_QUARANTINE.c.id == _DLQ_OUTBOX_EVENT.c.quarantine_id,
                )
            )
            .where(
                or_(
                    and_(
                        _DLQ_OUTBOX_EVENT.c.status == "PENDING",
                        _DLQ_OUTBOX_EVENT.c.available_at <= now,
                    ),
                    and_(
                        _DLQ_OUTBOX_EVENT.c.status == "CLAIMED",
                        _DLQ_OUTBOX_EVENT.c.claim_expires_at <= now,
                    ),
                )
            )
            .order_by(
                _DLQ_OUTBOX_EVENT.c.available_at,
                _DLQ_OUTBOX_EVENT.c.event_id,
            )
            .limit(1)
            .with_for_update(
                skip_locked=True,
                of=_DLQ_OUTBOX_EVENT,
            )
        )

        candidate_result = await self._session.execute(candidate_statement)
        candidate = candidate_result.mappings().one_or_none()

        if candidate is None:
            return None

        event_id = UUID(str(candidate["event_id"]))
        claim_token = uuid4().hex

        claim_statement = (
            update(_DLQ_OUTBOX_EVENT)
            .where(_DLQ_OUTBOX_EVENT.c.event_id == str(event_id))
            .values(
                status="CLAIMED",
                attempt_count=(_DLQ_OUTBOX_EVENT.c.attempt_count + 1),
                claim_token=claim_token,
                claim_expires_at=claim_expires_at,
                last_error_code=None,
                updated_at=now,
            )
            .returning(_DLQ_OUTBOX_EVENT.c.attempt_count)
        )

        claim_result = await self._session.execute(claim_statement)
        attempt_count = claim_result.scalar_one_or_none()

        if attempt_count is None:
            raise DlqOutboxStateError()

        envelope = DeadLetterEnvelope(
            event_id=event_id,
            quarantine_id=UUID(str(candidate["quarantine_id"])),
            stream_entry_id=str(candidate["stream_entry_id"]),
            message_digest=str(candidate["message_digest"]),
            failure_code=QuarantineFailureCode(str(candidate["failure_code"])),
            original_schema_version=(
                str(candidate["original_schema_version"]) if candidate["original_schema_version"] is not None else None
            ),
            trace_id=(str(candidate["trace_id"]) if candidate["trace_id"] is not None else None),
        )

        return ClaimedDlqEvent(
            envelope=envelope,
            claim_token=claim_token,
            attempt_count=int(attempt_count),
        )

    async def mark_published(
        self,
        *,
        event_id: UUID,
        claim_token: str,
        published_at: datetime,
    ) -> None:
        """현재 claim token이 일치할 때만 PUBLISHED로 전환합니다."""

        statement = (
            update(_DLQ_OUTBOX_EVENT)
            .where(
                _DLQ_OUTBOX_EVENT.c.event_id == str(event_id),
                _DLQ_OUTBOX_EVENT.c.status == "CLAIMED",
                _DLQ_OUTBOX_EVENT.c.claim_token == claim_token,
            )
            .values(
                status="PUBLISHED",
                claim_token=None,
                claim_expires_at=None,
                last_error_code=None,
                published_at=published_at,
                updated_at=published_at,
            )
        )

        result = await self._session.execute(statement)

        if getattr(result, "rowcount", 0) != 1:
            raise DlqOutboxStateError()

    async def reschedule(
        self,
        *,
        event_id: UUID,
        claim_token: str,
        available_at: datetime,
        error_code: str,
    ) -> None:
        """발행 실패한 동일 event를 다음 실행 시각으로 재예약합니다."""

        statement = (
            update(_DLQ_OUTBOX_EVENT)
            .where(
                _DLQ_OUTBOX_EVENT.c.event_id == str(event_id),
                _DLQ_OUTBOX_EVENT.c.status == "CLAIMED",
                _DLQ_OUTBOX_EVENT.c.claim_token == claim_token,
            )
            .values(
                status="PENDING",
                available_at=available_at,
                claim_token=None,
                claim_expires_at=None,
                last_error_code=error_code,
                published_at=None,
                updated_at=available_at,
            )
        )

        result = await self._session.execute(statement)

        if getattr(result, "rowcount", 0) != 1:
            raise DlqOutboxStateError()
