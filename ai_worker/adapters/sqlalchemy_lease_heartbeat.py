"""별도 SQLAlchemy Session으로 Worker lease heartbeat를 갱신합니다."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_job_execution_repository import (
    SqlAlchemyJobExecutionRepository,
)
from ai_worker.core.job_execution import (
    ExecutionLease,
    LeaseHeartbeatHandle,
)


class SqlAlchemyLeaseHeartbeatHandle:
    """하나의 실행 lease에 연결된 heartbeat 작업입니다."""

    def __init__(
        self,
        *,
        stop_event: asyncio.Event,
        task: asyncio.Task[bool],
    ) -> None:
        self._stop_event = stop_event
        self._task = task

    async def wait(self) -> bool:
        """소유권 상실 또는 heartbeat 실패까지 기다립니다."""

        return await asyncio.shield(self._task)

    async def stop(self) -> bool:
        """heartbeat를 종료하고 실행 소유권 유지 여부를 반환합니다."""

        self._stop_event.set()
        return await self._task


class SqlAlchemyLeaseHeartbeat:
    """Handler 실행 중 별도 Session transaction으로 lease를 연장합니다."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        lease_duration: timedelta,
        heartbeat_interval: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        if heartbeat_interval <= timedelta(0):
            raise ValueError("heartbeat_interval은 0보다 커야 합니다.")

        if heartbeat_interval >= lease_duration:
            raise ValueError("heartbeat_interval은 lease_duration보다 짧아야 합니다.")

        self._session_factory = session_factory
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._clock = clock

    async def start(
        self,
        lease: ExecutionLease,
    ) -> LeaseHeartbeatHandle:
        """실행별 heartbeat 작업을 시작합니다."""

        stop_event = asyncio.Event()
        task = asyncio.create_task(
            self._maintain(
                lease,
                stop_event=stop_event,
            )
        )

        return SqlAlchemyLeaseHeartbeatHandle(
            stop_event=stop_event,
            task=task,
        )

    async def _maintain(
        self,
        lease: ExecutionLease,
        *,
        stop_event: asyncio.Event,
    ) -> bool:
        current_lease = lease

        while True:
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._heartbeat_interval.total_seconds(),
                )
            except TimeoutError:
                pass
            else:
                return True

            async with self._session_factory() as session:
                repository = SqlAlchemyJobExecutionRepository(session)

                try:
                    refreshed = await repository.refresh_heartbeat(
                        current_lease,
                        now=self._clock(),
                        lease_duration=self._lease_duration,
                    )

                    if refreshed is None:
                        await session.rollback()
                        return False

                    await session.commit()
                except asyncio.CancelledError:
                    await session.rollback()
                    raise
                except Exception:
                    await session.rollback()
                    raise

            current_lease = refreshed
