from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.core.retry import FailureCode


@dataclass(frozen=True, slots=True)
class ExpiredExecution:
    job_id: UUID
    event_id: UUID
    attempt: int
    max_attempts: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ScheduledRetry:
    job_id: UUID
    event_id: UUID
    attempt: int
    available_at: datetime


class RecoveryDisposition(StrEnum):
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    NOT_RECOVERED = "NOT_RECOVERED"


class RecoveryRepository(Protocol):
    async def list_expired_executions(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[ExpiredExecution, ...]:
        """만료된 PROCESSING 실행을 조회합니다."""
        ...

    async def recover_expired_execution(
        self,
        execution: ExpiredExecution,
        *,
        now: datetime,
        retry_at: datetime,
        failure_code: FailureCode,
    ) -> RecoveryDisposition:
        """만료된 실행을 조건부로 RETRY_WAIT 또는 FAILED로 전환합니다."""
        ...

    async def schedule_due_retries(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[ScheduledRetry, ...]:
        """실행 시각이 지난 RETRY_WAIT Job의 다음 Outbox를 생성합니다."""
        ...
