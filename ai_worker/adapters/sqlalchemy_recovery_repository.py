"""SQLAlchemy 기반 만료 Worker 실행 복구 Repository입니다."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    and_,
    column,
    insert,
    select,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_ocr_failure import mark_linked_ocr_job_failed
from ai_worker.core.recovery import (
    ExpiredExecution,
    RecoveryDisposition,
    ScheduledRetry,
)
from ai_worker.core.retry import FailureCode

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
    column("failure_code", String(100)),
    column("completed_at", DateTime(timezone=True)),
)

_AI_JOB_ATTEMPT = table(
    "ai_job_attempt",
    column("id", String(36)),
    column("ai_job_id", String(36)),
    column("attempt_no", Integer),
    column("attempt_status", String(30)),
    column("error_code", String(100)),
    column("retryable", Boolean),
    column("timed_out", Boolean),
    column("completed_at", DateTime(timezone=True)),
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
    column("trace_id", String(100)),
    column("domain_type", String(20)),
    column("domain_id", String(36)),
)


class RecoveryStateError(RuntimeError):
    """Job과 Attempt를 함께 복구하지 못한 경우의 안전한 오류입니다."""

    def __init__(self) -> None:
        super().__init__("Worker 복구 상태 저장에 실패했습니다.")


class SqlAlchemyRecoveryRepository:
    """만료된 Worker 실행을 조회하고 조건부로 복구합니다.

    트랜잭션 commit과 rollback은 호출자가 담당합니다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_expired_executions(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[ExpiredExecution, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit은 정수여야 합니다.")
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다.")

        statement = (
            select(
                _AI_JOB.c.id,
                _AI_JOB.c.expected_event_id,
                _AI_JOB.c.attempt_count,
                _AI_JOB.c.max_attempts,
                _AI_JOB.c.lease_expires_at,
            )
            .where(
                _AI_JOB.c.status == "PROCESSING",
                _AI_JOB.c.expected_event_id.is_not(None),
                _AI_JOB.c.lease_expires_at.is_not(None),
                _AI_JOB.c.lease_expires_at <= now,
            )
            .order_by(
                _AI_JOB.c.lease_expires_at,
                _AI_JOB.c.id,
            )
            .limit(limit)
        )

        result = await self._session.execute(statement)
        rows = result.mappings().all()

        return tuple(
            ExpiredExecution(
                job_id=UUID(str(row["id"])),
                event_id=UUID(str(row["expected_event_id"])),
                attempt=int(row["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
                lease_expires_at=row["lease_expires_at"],
            )
            for row in rows
        )

    async def schedule_due_retries(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[ScheduledRetry, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit은 정수여야 합니다.")
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다.")

        candidate_statement = (
            select(
                _AI_JOB.c.id,
                _AI_JOB.c.attempt_count,
                _AI_JOB.c.max_attempts,
                _AI_JOB.c.available_at,
                _OUTBOX_EVENT.c.trace_id,
                _OUTBOX_EVENT.c.domain_type,
                _OUTBOX_EVENT.c.domain_id,
            )
            .select_from(
                _AI_JOB.join(
                    _OUTBOX_EVENT,
                    and_(
                        _OUTBOX_EVENT.c.event_id == _AI_JOB.c.last_consumed_event_id,
                        _OUTBOX_EVENT.c.job_id == _AI_JOB.c.id,
                        _OUTBOX_EVENT.c.attempt == _AI_JOB.c.attempt_count,
                    ),
                )
            )
            .where(
                _AI_JOB.c.status == "RETRY_WAIT",
                _AI_JOB.c.available_at <= now,
                _AI_JOB.c.expected_event_id.is_(None),
                _AI_JOB.c.attempt_count < _AI_JOB.c.max_attempts,
            )
            .order_by(
                _AI_JOB.c.available_at,
                _AI_JOB.c.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

        candidate_result = await self._session.execute(candidate_statement)
        rows = candidate_result.mappings().all()

        scheduled: list[ScheduledRetry] = []

        for row in rows:
            job_id = UUID(str(row["id"]))
            current_attempt = int(row["attempt_count"])
            next_attempt = current_attempt + 1
            event_id = uuid4()

            outbox_statement = insert(_OUTBOX_EVENT).values(
                event_id=str(event_id),
                job_id=str(job_id),
                attempt=next_attempt,
                event_kind="JOB_EXECUTE",
                schema_version="1.0",
                status="PENDING",
                available_at=now,
                trace_id=row["trace_id"],
                domain_type=row["domain_type"],
                domain_id=row["domain_id"],
            )
            await self._session.execute(outbox_statement)

            job_statement = (
                update(_AI_JOB)
                .where(
                    _AI_JOB.c.id == str(job_id),
                    _AI_JOB.c.status == "RETRY_WAIT",
                    _AI_JOB.c.expected_event_id.is_(None),
                    _AI_JOB.c.attempt_count == current_attempt,
                    _AI_JOB.c.attempt_count < _AI_JOB.c.max_attempts,
                    _AI_JOB.c.available_at <= now,
                )
                .values(
                    expected_event_id=str(event_id),
                )
                .returning(_AI_JOB.c.id)
            )

            job_result = await self._session.execute(job_statement)

            if job_result.scalar_one_or_none() is None:
                # 앞서 추가한 Outbox도 함께 rollback되어야 합니다.
                raise RecoveryStateError()

            scheduled.append(
                ScheduledRetry(
                    job_id=job_id,
                    event_id=event_id,
                    attempt=next_attempt,
                    available_at=now,
                )
            )

        return tuple(scheduled)

    async def recover_expired_execution(
        self,
        execution: ExpiredExecution,
        *,
        now: datetime,
        retry_at: datetime,
        failure_code: FailureCode,
    ) -> RecoveryDisposition:
        retryable = execution.attempt < execution.max_attempts
        next_status = "RETRY_WAIT" if retryable else "FAILED"

        job_statement = (
            update(_AI_JOB)
            .where(
                _AI_JOB.c.id == str(execution.job_id),
                _AI_JOB.c.status == "PROCESSING",
                _AI_JOB.c.expected_event_id == str(execution.event_id),
                _AI_JOB.c.attempt_count == execution.attempt,
                _AI_JOB.c.lease_expires_at <= now,
            )
            .values(
                status=next_status,
                last_consumed_event_id=str(execution.event_id),
                expected_event_id=None,
                available_at=retry_at if retryable else now,
                lease_token=None,
                lease_expires_at=None,
                heartbeat_at=None,
                failure_code=None if retryable else "RETRY_EXHAUSTED",
                completed_at=None if retryable else now,
            )
            .returning(_AI_JOB.c.job_type)
        )

        job_result = await self._session.execute(job_statement)
        job_type = job_result.scalar_one_or_none()

        if job_type is None:
            return RecoveryDisposition.NOT_RECOVERED

        attempt_statement = (
            update(_AI_JOB_ATTEMPT)
            .where(
                _AI_JOB_ATTEMPT.c.ai_job_id == str(execution.job_id),
                _AI_JOB_ATTEMPT.c.attempt_no == execution.attempt,
                _AI_JOB_ATTEMPT.c.attempt_status == "PROCESSING",
            )
            .values(
                attempt_status="FAILED",
                error_code=failure_code,
                retryable=retryable,
                timed_out=failure_code == "TIMEOUT",
                completed_at=now,
            )
            .returning(_AI_JOB_ATTEMPT.c.id)
        )

        attempt_result = await self._session.execute(attempt_statement)

        if attempt_result.scalar_one_or_none() is None:
            # 호출자가 이 예외를 받고 전체 트랜잭션을 rollback해야 합니다.
            raise RecoveryStateError()

        if not retryable and str(job_type) == "OCR":
            ocr_failed = await mark_linked_ocr_job_failed(
                self._session,
                ai_job_id=str(execution.job_id),
                failure_code=failure_code,
                completed_at=now,
            )
            if not ocr_failed:
                raise RecoveryStateError()

        if retryable:
            return RecoveryDisposition.RETRY_WAIT

        return RecoveryDisposition.FAILED
