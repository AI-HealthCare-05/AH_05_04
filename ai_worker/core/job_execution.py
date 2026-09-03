"""Worker Job lease·fencing Repository의 Core 계약입니다."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from ai_worker.schemas.messages import WorkerMessage


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    """Worker가 획득한 하나의 Job 실행 소유권입니다."""

    job_id: UUID
    event_id: UUID
    attempt: int
    lease_token: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class CommittedDelivery:
    """동일 event의 결과가 이미 commit된 전달입니다."""

    job_id: UUID
    event_id: UUID
    attempt: int


@dataclass(frozen=True, slots=True)
class LeaseNotAcquired:
    """조건 불일치 또는 동시 경합으로 lease를 얻지 못했습니다."""


type LeaseAcquisitionResult = ExecutionLease | CommittedDelivery | LeaseNotAcquired


class LeaseHeartbeatHandle(Protocol):
    """하나의 실행 lease에 대해 동작 중인 heartbeat입니다."""

    async def wait(self) -> bool:
        """heartbeat 종료를 기다리고 소유권 유지 여부를 반환합니다."""
        ...

    async def stop(self) -> bool:
        """heartbeat를 종료하고 마지막까지 소유권을 유지했는지 반환합니다."""
        ...


class LeaseHeartbeat(Protocol):
    """Handler 실행 중 별도 transaction으로 lease를 갱신합니다."""

    async def start(
        self,
        lease: ExecutionLease,
    ) -> LeaseHeartbeatHandle:
        """heartbeat를 시작하고 실행별 종료 handle을 반환합니다."""
        ...


class JobExecutionRepository(Protocol):
    """Job 실행 소유권과 terminal 상태를 관리하는 저장소 계약입니다.

    구현체는 주입된 transaction에 변경만 남기고 직접 commit하지 않습니다.
    """

    async def acquire_lease(
        self,
        message: WorkerMessage,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> LeaseAcquisitionResult:
        """동일 event commit 여부를 확인한 뒤 원자적으로 lease를 획득합니다."""
        ...

    async def refresh_heartbeat(
        self,
        lease: ExecutionLease,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> ExecutionLease | None:
        """현재 실행 소유자만 heartbeat와 lease 만료를 갱신합니다."""
        ...

    async def complete_execution(
        self,
        lease: ExecutionLease,
        *,
        completed_at: datetime,
    ) -> bool:
        """현재 실행 소유자만 Job과 소비 event를 완료 상태로 갱신합니다."""
        ...
