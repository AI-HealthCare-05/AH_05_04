"""PostgreSQL Outbox claim과 발행 완료 fencing adapter입니다."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, and_, column, or_, select, table, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_worker.core.outbox_publisher import ClaimedOutboxEvent

_AI_JOB = table(
    "ai_job",
    column("id", String(36)),
    column("job_type", String(20)),
)

_OUTBOX_EVENT = table(
    "outbox_event",
    column("event_id", String(36)),
    column("job_id", String(36)),
    column("attempt", Integer),
    column("event_kind", String(30)),
    column("schema_version", String(20)),
    column("status", String(20)),
    column("available_at", DateTime(timezone=True)),
    column("claim_token", String(100)),
    column("claim_expires_at", DateTime(timezone=True)),
    column("published_at", DateTime(timezone=True)),
    column("stream_message_id", String(100)),
    column("trace_id", String(100)),
    column("domain_type", String(20)),
    column("domain_id", String(36)),
)


class SqlAlchemyOutboxRepository:
    """각 claim·완료 작업을 독립된 짧은 transaction으로 commit합니다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_available(
        self,
        *,
        now: datetime,
        claim_token: str,
        claim_expires_at: datetime,
        limit: int,
    ) -> Sequence[ClaimedOutboxEvent]:
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다.")
        if not claim_token.strip():
            raise ValueError("claim_token은 비어 있을 수 없습니다.")
        if claim_expires_at <= now:
            raise ValueError("claim_expires_at은 now보다 뒤여야 합니다.")

        async with self._session_factory() as session, session.begin():
            due = or_(
                and_(
                    _OUTBOX_EVENT.c.status == "PENDING",
                    _OUTBOX_EVENT.c.available_at <= now,
                ),
                and_(
                    _OUTBOX_EVENT.c.status == "CLAIMED",
                    _OUTBOX_EVENT.c.claim_expires_at <= now,
                ),
            )
            statement = (
                select(_OUTBOX_EVENT, _AI_JOB.c.job_type)
                .select_from(_OUTBOX_EVENT.join(_AI_JOB, _AI_JOB.c.id == _OUTBOX_EVENT.c.job_id))
                .where(due)
                .order_by(_OUTBOX_EVENT.c.available_at, _OUTBOX_EVENT.c.event_id)
                .limit(limit)
                .with_for_update(of=_OUTBOX_EVENT, skip_locked=True)
            )
            rows = (await session.execute(statement)).mappings().all()

            if not rows:
                return ()

            event_ids = [row["event_id"] for row in rows]
            await session.execute(
                update(_OUTBOX_EVENT)
                .where(_OUTBOX_EVENT.c.event_id.in_(event_ids), due)
                .values(
                    status="CLAIMED",
                    claim_token=claim_token,
                    claim_expires_at=claim_expires_at,
                )
            )

            return tuple(self._to_claimed_event(row, claim_token=claim_token) for row in rows)

    async def mark_published(
        self,
        *,
        event_id: UUID,
        claim_token: str,
        stream_message_id: str,
        published_at: datetime,
    ) -> bool:
        if not stream_message_id.strip():
            raise ValueError("stream_message_id는 비어 있을 수 없습니다.")

        async with self._session_factory() as session, session.begin():
            statement = (
                update(_OUTBOX_EVENT)
                .where(
                    _OUTBOX_EVENT.c.event_id == str(event_id),
                    _OUTBOX_EVENT.c.status == "CLAIMED",
                    _OUTBOX_EVENT.c.claim_token == claim_token,
                )
                .values(
                    status="PUBLISHED",
                    published_at=published_at,
                    stream_message_id=stream_message_id,
                    claim_token=None,
                    claim_expires_at=None,
                )
                .returning(_OUTBOX_EVENT.c.event_id)
            )
            result = await session.execute(statement)
            return result.scalar_one_or_none() is not None

    @staticmethod
    def _to_claimed_event(mapping: RowMapping, *, claim_token: str) -> ClaimedOutboxEvent:
        return ClaimedOutboxEvent(
            event_id=UUID(str(mapping["event_id"])),
            job_id=UUID(str(mapping["job_id"])),
            job_type=str(mapping["job_type"]),
            event_kind=str(mapping["event_kind"]),
            schema_version=str(mapping["schema_version"]),
            domain_type=(None if mapping["domain_type"] is None else str(mapping["domain_type"])),
            domain_id=(None if mapping["domain_id"] is None else UUID(str(mapping["domain_id"]))),
            attempt=int(mapping["attempt"]),
            available_at=mapping["available_at"],
            trace_id=(None if mapping["trace_id"] is None else str(mapping["trace_id"])),
            claim_token=claim_token,
        )
