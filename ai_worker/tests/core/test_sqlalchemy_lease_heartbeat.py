"""별도 SQLAlchemy Session 기반 lease heartbeat를 검증합니다."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_lease_heartbeat import (
    SqlAlchemyLeaseHeartbeat,
)
from ai_worker.core.job_execution import ExecutionLease


def build_lease(now: datetime) -> ExecutionLease:
    return ExecutionLease(
        job_id=uuid4(),
        event_id=uuid4(),
        attempt=1,
        lease_token=uuid4().hex,
        lease_expires_at=now + timedelta(seconds=30),
    )


@pytest.mark.asyncio
async def test_heartbeat_refreshes_and_commits_in_factory_session() -> None:
    now = datetime.now(UTC)
    refresh_executed = asyncio.Event()

    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = uuid4().hex

    session_mock = AsyncMock(spec=AsyncSession)
    session_mock.__aenter__.return_value = session_mock

    async def execute_statement(_statement: object) -> MagicMock:
        refresh_executed.set()
        return update_result

    session_mock.execute.side_effect = execute_statement

    session_factory_mock = Mock(return_value=session_mock)
    session_factory = cast(
        Callable[[], AsyncSession],
        session_factory_mock,
    )

    heartbeat = SqlAlchemyLeaseHeartbeat(
        session_factory=session_factory,
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(milliseconds=1),
        clock=lambda: now,
    )

    handle = await heartbeat.start(build_lease(now))

    await asyncio.wait_for(
        refresh_executed.wait(),
        timeout=1,
    )
    ownership_retained = await handle.stop()

    assert ownership_retained is True
    assert session_factory_mock.call_count >= 1
    session_mock.commit.assert_awaited()
    session_mock.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_returns_false_when_fencing_update_affects_no_row() -> None:
    now = datetime.now(UTC)
    refresh_executed = asyncio.Event()

    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = None

    session_mock = AsyncMock(spec=AsyncSession)
    session_mock.__aenter__.return_value = session_mock

    async def execute_statement(_statement: object) -> MagicMock:
        refresh_executed.set()
        return update_result

    session_mock.execute.side_effect = execute_statement

    session_factory_mock = Mock(return_value=session_mock)
    session_factory = cast(
        Callable[[], AsyncSession],
        session_factory_mock,
    )

    heartbeat = SqlAlchemyLeaseHeartbeat(
        session_factory=session_factory,
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(milliseconds=1),
        clock=lambda: now,
    )

    handle = await heartbeat.start(build_lease(now))

    await asyncio.wait_for(
        refresh_executed.wait(),
        timeout=1,
    )
    ownership_retained = await handle.stop()

    assert ownership_retained is False
    session_mock.rollback.assert_awaited_once()
    session_mock.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_stops_and_propagates_refresh_failure() -> None:
    now = datetime.now(UTC)
    refresh_executed = asyncio.Event()

    session_mock = AsyncMock(spec=AsyncSession)
    session_mock.__aenter__.return_value = session_mock

    async def fail_execute(_statement: object) -> MagicMock:
        refresh_executed.set()
        raise RuntimeError("synthetic heartbeat database failure")

    session_mock.execute.side_effect = fail_execute

    session_factory_mock = Mock(return_value=session_mock)
    session_factory = cast(
        Callable[[], AsyncSession],
        session_factory_mock,
    )

    heartbeat = SqlAlchemyLeaseHeartbeat(
        session_factory=session_factory,
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(milliseconds=1),
        clock=lambda: now,
    )

    handle = await heartbeat.start(build_lease(now))

    await asyncio.wait_for(
        refresh_executed.wait(),
        timeout=1,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic heartbeat database failure",
    ):
        await handle.stop()

    session_mock.rollback.assert_awaited_once()
    session_mock.commit.assert_not_awaited()
