from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core import config
from app.core.utils.idempotency import (
    compute_key_hmac,
    compute_request_hash,
    validate_idempotency_key_format,
)
from app.models.async_jobs import AiJob, AiJobType, IdempotencyRecord
from app.repositories.async_job_repository import (
    AsyncJobRepository,
    is_async_idempotency_scope_conflict,
)

CreateDomainPlaceholder = Callable[[UUID], Awaitable[None]]


class IdempotencyKeyConflictError(Exception):
    """같은 `Idempotency-Key`로 다른 요청 지문이 접수됐을 때 발생합니다(`409 IDEMPOTENCY_KEY_CONFLICT`)."""


@dataclass
class JobIntakeResult:
    job: AiJob
    # True면 새 Job을 만들지 않고 기존 접수의 현재 상태를 그대로 반환한 것입니다.
    is_duplicate: bool


class JobIntakeService:
    """OCR·Guide·Chat 공통 Job 접수 transaction(idempotency-v1.md, async-job-v1.md)을 구현합니다.

    도메인 placeholder(예: `OcrJob`, `Guide`, `CHAT_MESSAGE`) 생성은 호출자가
    `create_domain_placeholder` 콜백으로 제공합니다 — 이 service는 새로 생성된 `AiJob.id`를
    콜백에 전달해 도메인 row가 `ai_job_id` FK를 채우도록 하고, 나머지 공통 부분
    (`AI_JOB`, `OUTBOX_EVENT`, `IDEMPOTENCY_RECORD`)만 책임집니다.

    성공 경로는 `flush()`만 호출하고 절대 직접 `commit()`하지 않습니다 — 최종 commit은
    `get_db_session`이 요청 끝에 한 번만 수행해야 "commit 실패 시 Outbox 미발행"이 보장됩니다.
    """

    def __init__(self, repository: AsyncJobRepository) -> None:
        self.repository = repository

    async def accept_job(
        self,
        *,
        user_id: UUID,
        job_type: AiJobType,
        operation_id: str,
        idempotency_key: str,
        fingerprint: dict[str, Any],
        create_domain_placeholder: CreateDomainPlaceholder,
        prescription_version_id: UUID | None = None,
        max_attempts: int | None = None,
    ) -> JobIntakeResult:
        validate_idempotency_key_format(idempotency_key)

        key_hmac = compute_key_hmac(idempotency_key, hmac_key=config.IDEMPOTENCY_HMAC_KEY)
        request_hash = compute_request_hash(fingerprint)

        existing = await self.repository.find_async_idempotency_record(
            user_id=user_id,
            operation_id=operation_id,
            key_hmac=key_hmac,
        )
        if existing is not None:
            return await self._resolve_existing_record(existing, request_hash=request_hash)

        session = self.repository.session
        try:
            async with session.begin_nested():
                job = await self.repository.create_job(
                    user_id=user_id,
                    job_type=job_type,
                    prescription_version_id=prescription_version_id,
                    max_attempts=max_attempts,
                )
                await create_domain_placeholder(job.id)
                await self.repository.create_outbox_event(job=job)
                await self.repository.create_async_idempotency_record(
                    user_id=user_id,
                    operation_id=operation_id,
                    key_hmac=key_hmac,
                    request_hash=request_hash,
                    job_id=job.id,
                )
        except IntegrityError as exc:
            if not is_async_idempotency_scope_conflict(exc):
                raise
            # 동시 최초 요청 경쟁 — 패자는 승자가 저장한 레코드를 다시 조회해 지문을 비교합니다
            # (idempotency-v1.md: "동시 최초 요청은 DB unique constraint로 하나만 승리시킨 뒤,
            # 패자는 저장된 요청 지문을 비교해 규칙을 적용한다").
            existing = await self.repository.find_async_idempotency_record(
                user_id=user_id,
                operation_id=operation_id,
                key_hmac=key_hmac,
            )
            if existing is None:
                raise
            return await self._resolve_existing_record(existing, request_hash=request_hash)

        return JobIntakeResult(job=job, is_duplicate=False)

    async def _resolve_existing_record(
        self,
        record: IdempotencyRecord,
        *,
        request_hash: str,
    ) -> JobIntakeResult:
        if record.request_hash != request_hash:
            raise IdempotencyKeyConflictError("IDEMPOTENCY_KEY_CONFLICT")

        if record.job_id is None:
            raise RuntimeError("ASYNC_JOB idempotency record must reference a job_id")
        job = await self.repository.get_job(job_id=record.job_id)
        if job is None:
            raise RuntimeError(f"Idempotency record references missing AiJob: {record.job_id}")

        return JobIntakeResult(job=job, is_duplicate=True)
