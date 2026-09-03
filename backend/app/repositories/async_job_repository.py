from collections.abc import Iterator
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.async_jobs import (
    AiJob,
    AiJobStatus,
    AiJobType,
    DomainType,
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

    async def delete_expired_idempotency_record(self, *, record_id: UUID) -> None:
        """idempotency-v1.md "만료 이후 같은 키는 새 요청으로 처리될 수 있다": `expires_at`은 unique
        index를 자동 해제하지 않으므로, 만료 row를 먼저 원자적으로 제거해야 같은 key로 새 Job을 만들
        수 있습니다. `expires_at`을 다시 확인해, 그 사이 다른 요청이 이미 지웠거나 레코드가 더는
        만료 상태가 아니면(이론상 불가능하지만) 조건 불일치로 아무것도 지우지 않습니다."""
        now = datetime.now(config.TIMEZONE)
        await self.session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.expires_at <= now,
            )
        )
        await self.session.flush()

    async def get_job(self, *, job_id: UUID) -> AiJob | None:
        result = await self.session.execute(select(AiJob).where(AiJob.id == job_id))
        return result.scalars().first()

    async def get_interim_domain_reference(self, *, job: AiJob) -> tuple[DomainType, UUID] | None:
        """임시 구현 — `GET /jobs/{job_id}`의 `domain_type`/`domain_id`/`result_url` 구성과
        소유권 이중 확인(track-a-migration-rollback-v1.md §6)에 필요한 값이지만,
        `ocr_job`/`guide`/`chat_message`의 `ai_job_id` 역참조가 아직 없습니다(OCR은 #212,
        Guide·Chat은 이슈 미생성). 정식 값의 원본은 도메인 row의 `ai_job_id`여야 하지만,
        그 컬럼이 생기기 전까지는 접수 시점에 채운 `outbox_event.domain_type`/`domain_id`를
        `job.expected_event_id`로 따라가 대신 씁니다. Outbox는 30일 보존이라 그 이후
        삭제되면(`ON DELETE SET NULL`) 이 경로로는 값을 찾을 수 없습니다 — 도메인 row에
        `ai_job_id`가 추가되면 이 메서드를 역조회로 교체해야 합니다.
        """
        if job.expected_event_id is None:
            return None
        event = await self.session.get(OutboxEvent, job.expected_event_id)
        if event is None or event.domain_type is None or event.domain_id is None:
            return None
        return (event.domain_type, event.domain_id)

    async def get_latest_job_id_for_domain(self, *, domain_type: DomainType, domain_id: UUID) -> UUID | None:
        """`get_interim_domain_reference()`의 역방향입니다. rediscovery(#148)는 도메인 row(예:
        `ocr_job.id`)로부터 `job_id`를 찾아야 하는데, 그 도메인 row에는 아직 `ai_job_id`가 없어서
        (위 메서드와 같은 이유) 접수 시점에 채운 `outbox_event.domain_type`/`domain_id`로 대신
        역조회합니다. 같은 한계(Outbox 30일 보존 이후에는 조회 불가)가 적용됩니다.
        """
        result = await self.session.execute(
            select(OutboxEvent.job_id)
            .where(OutboxEvent.domain_type == domain_type, OutboxEvent.domain_id == domain_id)
            .order_by(OutboxEvent.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

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

    async def create_outbox_event(
        self,
        *,
        job: AiJob,
        trace_id: str,
        domain_type: DomainType,
        domain_id: UUID,
    ) -> OutboxEvent:
        event = OutboxEvent(
            job_id=job.id,
            attempt=1,
            event_kind=OutboxEventKind.JOB_EXECUTE,
            status=OutboxEventStatus.PENDING,
            trace_id=trace_id,
            domain_type=domain_type,
            domain_id=domain_id,
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
