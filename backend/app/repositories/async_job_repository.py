from collections.abc import Iterator
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.async_jobs import (
    AiJob,
    AiJobStatus,
    AiJobType,
    IdempotencyRecord,
    IdempotencyRecordType,
    OutboxEvent,
    OutboxEventKind,
    OutboxEventStatus,
)

# async-job-v1.md "시도와 재시도": 최초 실행을 포함한 기본 max_attempts입니다.
DEFAULT_MAX_ATTEMPTS: dict[AiJobType, int] = {
    AiJobType.OCR: 3,
    AiJobType.GUIDE: 3,
    AiJobType.CHAT: 2,
}

POSTGRES_UNIQUE_VIOLATION_SQLSTATE = "23505"
ASYNC_IDEMPOTENCY_SCOPE_KEY = "uq_idempotency_async_scope"


def _iter_database_errors(exc: IntegrityError) -> Iterator[BaseException]:
    """SQLAlchemy wrapper와 원본 DB 예외를 순서대로 확인합니다(민감정보 없이 구조화된 속성만 사용)."""
    error: BaseException | None = exc
    while error is not None:
        yield error
        error = error.__cause__


def is_async_idempotency_scope_conflict(exc: IntegrityError) -> bool:
    """동시 최초 요청 경쟁으로 `uq_idempotency_async_scope` unique index를 위반했는지 확인합니다."""
    for error in _iter_database_errors(exc):
        sqlstate = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
        constraint_name = getattr(error, "constraint_name", None)
        if sqlstate == POSTGRES_UNIQUE_VIOLATION_SQLSTATE and constraint_name == ASYNC_IDEMPOTENCY_SCOPE_KEY:
            return True
    return False


class AsyncJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def default_max_attempts(self, job_type: AiJobType) -> int:
        return DEFAULT_MAX_ATTEMPTS[job_type]

    async def find_async_idempotency_record(
        self,
        *,
        user_id: UUID,
        operation_id: str,
        key_hmac: str,
    ) -> IdempotencyRecord | None:
        result = await self.session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.record_type == IdempotencyRecordType.ASYNC_JOB,
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.operation_id == operation_id,
                IdempotencyRecord.key_hmac == key_hmac,
            )
        )
        return result.scalars().first()

    async def get_job(self, *, job_id: UUID) -> AiJob | None:
        result = await self.session.execute(select(AiJob).where(AiJob.id == job_id))
        return result.scalars().first()

    async def create_job(
        self,
        *,
        user_id: UUID,
        job_type: AiJobType,
        prescription_version_id: UUID | None,
        max_attempts: int | None = None,
    ) -> AiJob:
        job = AiJob(
            user_id=user_id,
            job_type=job_type,
            status=AiJobStatus.PENDING,
            prescription_version_id=prescription_version_id,
            max_attempts=max_attempts if max_attempts is not None else self.default_max_attempts(job_type),
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def create_outbox_event(self, *, job: AiJob) -> OutboxEvent:
        event = OutboxEvent(
            job_id=job.id,
            attempt=1,
            event_kind=OutboxEventKind.JOB_EXECUTE,
            status=OutboxEventStatus.PENDING,
        )
        self.session.add(event)
        await self.session.flush()

        job.expected_event_id = event.event_id
        await self.session.flush()

        return event

    async def create_async_idempotency_record(
        self,
        *,
        user_id: UUID,
        operation_id: str,
        key_hmac: str,
        request_hash: str,
        job_id: UUID,
    ) -> IdempotencyRecord:
        now = datetime.now(config.TIMEZONE)
        record = IdempotencyRecord(
            user_id=user_id,
            operation_id=operation_id,
            key_hmac_version=config.IDEMPOTENCY_HMAC_KEY_VERSION,
            key_hmac=key_hmac,
            request_hash=request_hash,
            record_type=IdempotencyRecordType.ASYNC_JOB,
            job_id=job_id,
            expires_at=now + timedelta(days=config.IDEMPOTENCY_RECORD_TTL_DAYS),
        )
        self.session.add(record)
        await self.session.flush()
        return record
