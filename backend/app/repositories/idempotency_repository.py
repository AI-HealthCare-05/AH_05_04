from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.async_jobs import IdempotencyRecord, IdempotencyRecordType
from app.repositories.async_job_repository import (
    POSTGRES_UNIQUE_VIOLATION_SQLSTATE,
    _iter_database_errors,
)

SYNC_IDEMPOTENCY_SCOPE_KEY = "uq_idempotency_sync_scope"


def is_sync_idempotency_scope_conflict(exc: IntegrityError) -> bool:
    """동시 최초 요청 경쟁으로 `uq_idempotency_sync_scope` unique index를 위반했는지 확인합니다
    (`is_async_idempotency_scope_conflict`와 동일한 판정을 SYNC_MUTATION scope로 적용)."""
    for error in _iter_database_errors(exc):
        sqlstate = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
        constraint_name = getattr(error, "constraint_name", None)
        if sqlstate == POSTGRES_UNIQUE_VIOLATION_SQLSTATE and constraint_name == SYNC_IDEMPOTENCY_SCOPE_KEY:
            return True
    return False


class IdempotencyRepository:
    """`record_type=SYNC_MUTATION`(idempotency-v1.md) 조회·저장을 담당합니다.

    ASYNC_JOB 경로(`AsyncJobRepository`)와 같은 `IdempotencyRecord` 테이블을 쓰지만
    scope(`parent_resource_id` 포함)와 payload 컬럼(`response_status`,
    `response_body_snapshot`, `encryption_key_version`)이 달라 별도 repository로 둔다.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_sync_idempotency_record(
        self,
        *,
        user_id: UUID,
        operation_id: str,
        parent_resource_id: UUID,
        key_hmac: str,
    ) -> IdempotencyRecord | None:
        result = await self.session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.record_type == IdempotencyRecordType.SYNC_MUTATION,
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.operation_id == operation_id,
                IdempotencyRecord.parent_resource_id == parent_resource_id,
                IdempotencyRecord.key_hmac == key_hmac,
            )
        )
        return result.scalars().first()

    async def delete_expired_idempotency_record(self, *, record_id: UUID) -> None:
        """`AsyncJobRepository.delete_expired_idempotency_record`와 동일한 이유로, 만료된
        row를 원자적으로 먼저 제거해야 같은 key로 새 mutation을 재시도할 수 있다. `expires_at`을
        다시 확인해, 그 사이 다른 요청이 이미 지웠거나 더는 만료 상태가 아니면 아무것도
        지우지 않는다."""
        now = datetime.now(config.TIMEZONE)
        await self.session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.expires_at <= now,
            )
        )
        await self.session.flush()

    async def create_sync_idempotency_record(
        self,
        *,
        user_id: UUID,
        operation_id: str,
        parent_resource_id: UUID,
        key_hmac: str,
        request_hash: str,
        response_status: int,
        response_body_snapshot: bytes,
        encryption_key_version: str,
    ) -> IdempotencyRecord:
        now = datetime.now(config.TIMEZONE)
        record = IdempotencyRecord(
            user_id=user_id,
            operation_id=operation_id,
            key_hmac_version=config.IDEMPOTENCY_HMAC_KEY_VERSION,
            key_hmac=key_hmac,
            request_hash=request_hash,
            record_type=IdempotencyRecordType.SYNC_MUTATION,
            parent_resource_id=parent_resource_id,
            response_status=response_status,
            response_body_snapshot=response_body_snapshot,
            encryption_key_version=encryption_key_version,
            expires_at=now + timedelta(days=config.IDEMPOTENCY_RECORD_TTL_DAYS),
        )
        self.session.add(record)
        await self.session.flush()
        return record
