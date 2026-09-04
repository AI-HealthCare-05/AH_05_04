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
        """임시 구현(fallback 전용) — `GET /jobs/{job_id}`의 `domain_type`/`domain_id`/
        `result_url` 구성과 소유권 이중 확인(track-a-migration-rollback-v1.md §6)에 필요한
        값입니다. 정식 값의 원본은 도메인 row의 영속 `ai_job_id` 역참조이며, OCR은 #212로
        `ocr_job.ai_job_id`가, Guide도 같은 목적으로 `guide.ai_job_id`가 이미 있어
        `JobStatusService._resolve_domain_reference()`가 이 메서드보다 그 값을 우선
        사용합니다(값이 채워진 뒤에만) — 이 메서드는 그 값이 아직 없는 OCR·Guide row와,
        `ai_job_id`가 아직 없는 Chat(이슈 미생성)에만 쓰입니다. 그 경우 `job.id`로 이 Job의
        `outbox_event`를 직접 조회해 대신 씁니다. Outbox는 30일 보존이라 그 이후
        삭제되면(`ON DELETE SET NULL`) 이 경로로는 값을 찾을 수 없습니다 — Chat도 도메인
        row에 `ai_job_id`가 추가되면 이 fallback 의존을 없애야 합니다.

        Job 실행 메타데이터는 terminal 후 90일 보존이라, `COMPLETED`/`FAILED`/`STALE` Job이
        Outbox 보존 기간(30일)은 지났지만 Job 보존 기간(90일)은 아직 안 지난 31~90일째에는
        Job은 남아 있어도 이 메서드가 `None`을 반환해 `GET /jobs/{job_id}`가 `404`가
        됩니다 — `job_id` 기준 조회로 바꿔도 Outbox row 자체가 사라지므로 해결되지 않고,
        도메인 row에 `ai_job_id`가 채워지는 것만이 이 gap의 답입니다(OCR·Guide는 이미 컬럼이
        있으므로 접수가 그 값을 채우면, Chat은 같은 목적의 컬럼이 추가되면).

        `job.expected_event_id`는 쓰지 않습니다 — outbox-stream-v1.md §소비와 fencing에 따라
        Reconciler가 다음 attempt Outbox를 만들기 전까지(`RETRY_WAIT` 전환 직후, 또는 재시도
        소진 뒤 `FAILED`로 종결된 이후)는 fencing이 깨지지 않도록 이 값이 `NULL`로 비어
        있습니다. 그 값을 신원 조회 기준으로 쓰면 이 두 구간에서 `GET /jobs/{job_id}`가
        `404`가 됩니다. `job.id`로 직접 조회하면 attempt가 몇 번 진행돼도, Reconciler가 아직
        다음 attempt Outbox를 만들지 않았어도 항상 값을 찾습니다. `domain_type`/`domain_id`가
        `NOT NULL`인 row만 걸러 최신 attempt를 선택하므로, 재시도 Outbox가 아직 직전 event의
        `domain_type`/`domain_id`를 복사하지 않는 상태(#142 진행 중)에서도 attempt 1의 값으로
        안전하게 fallback합니다.
        """
        result = await self.session.execute(
            select(OutboxEvent.domain_type, OutboxEvent.domain_id)
            .where(
                OutboxEvent.job_id == job.id,
                OutboxEvent.domain_type.is_not(None),
                OutboxEvent.domain_id.is_not(None),
            )
            .order_by(OutboxEvent.attempt.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        return (row.domain_type, row.domain_id)

    async def get_latest_job_id_for_domain(self, *, domain_type: DomainType, domain_id: UUID) -> UUID | None:
        """`get_interim_domain_reference()`의 역방향이며 같은 이유로 fallback 전용입니다 —
        rediscovery(#148)가 도메인 row(예: `ocr_job.id`, `guide.id`)로부터 `job_id`를 찾을 때,
        영속 `ai_job_id`가 없는 경우(`rediscover_ocr_job`/`rediscover_guide_job` 참고)에만
        씁니다."""
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
