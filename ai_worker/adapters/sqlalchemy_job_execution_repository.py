"""SQLAlchemy 기반 Worker Job lease·fencing Repository입니다."""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    and_,
    column,
    exists,
    func,
    insert,
    select,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core.job_execution import (
    CommittedDelivery,
    ExecutionLease,
    LeaseAcquisitionResult,
    LeaseNotAcquired,
    LeaseRejectionReason,
)
from ai_worker.schemas.messages import WorkerMessage

_AI_JOB = table(
    "ai_job",
    column("id", String(36)),
    column("job_type", String(20)),
    column("status", String(20)),
    column("expected_event_id", String(36)),
    column("last_consumed_event_id", String(36)),
    column("attempt_count", Integer),
    column("max_attempts", Integer),
    column("available_at", DateTime(timezone=True)),
    column("lease_token", String(100)),
    column("lease_expires_at", DateTime(timezone=True)),
    column("heartbeat_at", DateTime(timezone=True)),
    column("started_at", DateTime(timezone=True)),
    column("completed_at", DateTime(timezone=True)),
)

_OUTBOX_EVENT = table(
    "outbox_event",
    column("event_id", String(36)),
    column("job_id", String(36)),
    column("attempt", Integer),
    column("event_kind", String(30)),
)

_AI_JOB_ATTEMPT = table(
    "ai_job_attempt",
    column("id", String(36)),
    column("ai_job_id", String(36)),
    column("attempt_no", Integer),
    column("attempt_status", String(30)),
    column("retryable", Boolean),
    column("timed_out", Boolean),
    column("started_at", DateTime(timezone=True)),
    column("completed_at", DateTime(timezone=True)),
)


class SqlAlchemyJobExecutionRepository:
    """주입된 session에서 Job 실행 소유권을 관리합니다.

    transaction commit은 호출자인 Worker가 담당합니다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_committed_delivery(
        self,
        message: WorkerMessage,
    ) -> CommittedDelivery | None:
        """Job·Outbox 연결까지 일치하는 이미 처리된 event를 찾습니다."""

        job_id = str(message.job_id)
        event_id = str(message.event_id)

        statement = (
            select(_OUTBOX_EVENT.c.attempt)
            .select_from(
                _OUTBOX_EVENT.join(
                    _AI_JOB,
                    _AI_JOB.c.id == _OUTBOX_EVENT.c.job_id,
                )
            )
            .where(
                _AI_JOB.c.id == job_id,
                _AI_JOB.c.status.in_(
                    (
                        "RETRY_WAIT",
                        "COMPLETED",
                        "FAILED",
                        "STALE",
                    )
                ),
                _AI_JOB.c.last_consumed_event_id == event_id,
                _OUTBOX_EVENT.c.event_id == event_id,
                _OUTBOX_EVENT.c.job_id == job_id,
                _OUTBOX_EVENT.c.attempt == message.attempt,
            )
        )

        result = await self._session.execute(statement)
        attempt = result.scalar_one_or_none()

        if attempt is None:
            return None

        return CommittedDelivery(
            job_id=message.job_id,
            event_id=message.event_id,
            attempt=attempt,
        )

    async def acquire_lease(
        self,
        message: WorkerMessage,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> LeaseAcquisitionResult:
        """진입 조건 전체를 포함한 단일 UPDATE로 lease를 획득합니다."""

        committed = await self.find_committed_delivery(message)

        if committed is not None:
            return committed

        if message.available_at > now:
            return LeaseNotAcquired()

        job_id = str(message.job_id)
        event_id = str(message.event_id)
        rejection_reason = await self._classify_poison_message(
            message,
        )

        if rejection_reason is not None:
            return LeaseNotAcquired(
                rejection_reason=rejection_reason,
            )

        lease_token = uuid4().hex
        lease_expires_at = now + lease_duration

        matching_outbox = exists(
            select(1)
            .select_from(_OUTBOX_EVENT)
            .where(
                _OUTBOX_EVENT.c.event_id == event_id,
                _OUTBOX_EVENT.c.job_id == job_id,
                _OUTBOX_EVENT.c.attempt == message.attempt,
                _OUTBOX_EVENT.c.event_kind == message.event_kind,
            )
        )

        lease_statement = (
            update(_AI_JOB)
            .where(
                _AI_JOB.c.id == job_id,
                _AI_JOB.c.job_type == message.job_type.value,
                _AI_JOB.c.expected_event_id == event_id,
                _AI_JOB.c.attempt_count == message.attempt - 1,
                _AI_JOB.c.attempt_count < _AI_JOB.c.max_attempts,
                _AI_JOB.c.available_at <= now,
                _AI_JOB.c.status.in_(("PENDING", "RETRY_WAIT")),
                matching_outbox,
            )
            .values(
                status="PROCESSING",
                attempt_count=message.attempt,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
                heartbeat_at=now,
                started_at=func.coalesce(_AI_JOB.c.started_at, now),
            )
            .returning(_AI_JOB.c.id)
        )

        update_result = await self._session.execute(lease_statement)
        acquired_job_id = update_result.scalar_one_or_none()

        if acquired_job_id is None:
            return LeaseNotAcquired()

        attempt_statement = insert(_AI_JOB_ATTEMPT).values(
            id=uuid4().hex,
            ai_job_id=job_id,
            attempt_no=message.attempt,
            attempt_status="PROCESSING",
            retryable=False,
            timed_out=False,
            started_at=now,
        )
        await self._session.execute(attempt_statement)

        return ExecutionLease(
            job_id=message.job_id,
            event_id=message.event_id,
            attempt=message.attempt,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
        )

    async def _classify_poison_message(
        self,
        message: WorkerMessage,
    ) -> LeaseRejectionReason | None:
        job_id = str(message.job_id)
        event_id = str(message.event_id)
        statement = (
            select(
                _AI_JOB.c.expected_event_id,
                _AI_JOB.c.attempt_count,
                _AI_JOB.c.status,
                _AI_JOB.c.job_type,
                _OUTBOX_EVENT.c.event_id.label("outbox_event_id"),
                _OUTBOX_EVENT.c.attempt.label("outbox_attempt"),
                _OUTBOX_EVENT.c.event_kind.label("outbox_event_kind"),
            )
            .select_from(
                _AI_JOB.outerjoin(
                    _OUTBOX_EVENT,
                    and_(
                        _OUTBOX_EVENT.c.event_id == event_id,
                        _OUTBOX_EVENT.c.job_id == job_id,
                    ),
                )
            )
            .where(_AI_JOB.c.id == job_id)
        )
        result = await self._session.execute(statement)
        row = result.mappings().one_or_none()

        if row is None:
            return LeaseRejectionReason.JOB_NOT_FOUND

        if (
            str(row["expected_event_id"]) != event_id
            or str(row["job_type"]) != message.job_type.value
            or row["outbox_event_id"] is None
            or str(row["outbox_event_kind"]) != message.event_kind
        ):
            return LeaseRejectionReason.EVENT_MISMATCH

        if int(row["outbox_attempt"]) != message.attempt:
            return LeaseRejectionReason.ATTEMPT_MISMATCH

        current_attempt = int(row["attempt_count"])

        # 다른 Worker가 같은 event의 lease를 먼저 획득한 정상 경합입니다.
        if str(row["status"]) == "PROCESSING" and current_attempt == message.attempt:
            return None

        if current_attempt != message.attempt - 1:
            return LeaseRejectionReason.ATTEMPT_MISMATCH

        return None

    async def refresh_heartbeat(
        self,
        lease: ExecutionLease,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ExecutionLease | None:
        """현재 attempt·token의 만료되지 않은 소유권만 연장합니다."""

        lease_expires_at = now + lease_duration

        statement = (
            update(_AI_JOB)
            .where(
                _AI_JOB.c.id == str(lease.job_id),
                _AI_JOB.c.attempt_count == lease.attempt,
                _AI_JOB.c.lease_token == lease.lease_token,
                _AI_JOB.c.status == "PROCESSING",
                _AI_JOB.c.lease_expires_at > now,
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=lease_expires_at,
            )
            .returning(_AI_JOB.c.id)
        )

        result = await self._session.execute(statement)
        refreshed_job_id = result.scalar_one_or_none()

        if refreshed_job_id is None:
            return None

        return ExecutionLease(
            job_id=lease.job_id,
            event_id=lease.event_id,
            attempt=lease.attempt,
            lease_token=lease.lease_token,
            lease_expires_at=lease_expires_at,
        )

    async def complete_execution(
        self,
        lease: ExecutionLease,
        *,
        completed_at: datetime,
    ) -> bool:
        """유효한 실행 소유자만 Job과 Attempt를 완료 상태로 변경합니다."""

        job_statement = (
            update(_AI_JOB)
            .where(
                _AI_JOB.c.id == str(lease.job_id),
                _AI_JOB.c.attempt_count == lease.attempt,
                _AI_JOB.c.lease_token == lease.lease_token,
                _AI_JOB.c.status == "PROCESSING",
                _AI_JOB.c.lease_expires_at > completed_at,
            )
            .values(
                status="COMPLETED",
                last_consumed_event_id=str(lease.event_id),
                completed_at=completed_at,
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(_AI_JOB.c.id)
        )

        job_result = await self._session.execute(job_statement)

        if job_result.scalar_one_or_none() is None:
            return False

        attempt_statement = (
            update(_AI_JOB_ATTEMPT)
            .where(
                _AI_JOB_ATTEMPT.c.ai_job_id == str(lease.job_id),
                _AI_JOB_ATTEMPT.c.attempt_no == lease.attempt,
                _AI_JOB_ATTEMPT.c.attempt_status == "PROCESSING",
            )
            .values(
                attempt_status="COMPLETED",
                completed_at=completed_at,
            )
            .returning(_AI_JOB_ATTEMPT.c.attempt_no)
        )

        attempt_result = await self._session.execute(attempt_statement)

        return attempt_result.scalar_one_or_none() is not None
