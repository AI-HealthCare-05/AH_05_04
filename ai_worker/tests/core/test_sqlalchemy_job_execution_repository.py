"""SQLAlchemy Job 실행 Repository 단위 테스트입니다."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_job_execution_repository import (
    SqlAlchemyJobExecutionRepository,
)
from ai_worker.core.job_execution import (
    CommittedDelivery,
    ExecutionLease,
    LeaseNotAcquired,
)
from ai_worker.schemas.messages import WorkerMessage


def build_message() -> WorkerMessage:
    now = datetime.now(UTC)

    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(uuid4()),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(uuid4()),
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": uuid4().hex,
        }
    )


@pytest.mark.asyncio
async def test_committed_delivery_is_returned_before_new_lease() -> None:
    message = build_message()
    session = AsyncMock(spec=AsyncSession)
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = message.attempt
    session.execute.return_value = query_result

    repository = SqlAlchemyJobExecutionRepository(session)

    result = await repository.acquire_lease(
        message,
        now=datetime.now(UTC),
        lease_duration=timedelta(seconds=30),
    )

    assert result == CommittedDelivery(
        job_id=message.job_id,
        event_id=message.event_id,
        attempt=message.attempt,
    )
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_job_acquires_first_lease_atomically() -> None:
    message = build_message()
    now = datetime.now(UTC)
    lease_duration = timedelta(seconds=30)

    session = AsyncMock(spec=AsyncSession)

    committed_result = MagicMock()
    committed_result.scalar_one_or_none.return_value = None

    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = str(message.job_id)

    session.execute.side_effect = [
        committed_result,
        update_result,
        MagicMock(),
    ]

    repository = SqlAlchemyJobExecutionRepository(session)

    result = await repository.acquire_lease(
        message,
        now=now,
        lease_duration=lease_duration,
    )

    assert isinstance(result, ExecutionLease)
    assert result.job_id == message.job_id
    assert result.event_id == message.event_id
    assert result.attempt == message.attempt
    assert result.lease_expires_at == now + lease_duration
    assert len(result.lease_token) == 32
    assert session.execute.await_count == 3

    lease_statement = session.execute.await_args_list[1].args[0]
    lease_sql = str(lease_statement)
    where_sql = lease_sql.partition(" WHERE ")[2]

    assert "UPDATE ai_job SET" in lease_sql
    assert "ai_job.id" in where_sql
    assert "ai_job.job_type" in where_sql
    assert "ai_job.expected_event_id" in where_sql
    assert "ai_job.attempt_count" in where_sql
    assert "ai_job.max_attempts" in where_sql
    assert "ai_job.available_at" in where_sql
    assert "ai_job.status" in where_sql
    assert "outbox_event.event_id" in where_sql
    assert "outbox_event.job_id" in where_sql
    assert "outbox_event.attempt" in where_sql
    assert "FOR UPDATE" not in lease_sql


@pytest.mark.asyncio
async def test_zero_row_update_does_not_create_attempt() -> None:
    message = build_message()
    session = AsyncMock(spec=AsyncSession)

    committed_result = MagicMock()
    committed_result.scalar_one_or_none.return_value = None

    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = None

    session.execute.side_effect = [
        committed_result,
        update_result,
    ]

    repository = SqlAlchemyJobExecutionRepository(session)

    result = await repository.acquire_lease(
        message,
        now=datetime.now(UTC),
        lease_duration=timedelta(seconds=30),
    )

    assert isinstance(result, LeaseNotAcquired)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_current_lease_refreshes_heartbeat_conditionally() -> None:
    message = build_message()
    now = datetime.now(UTC)
    lease_duration = timedelta(seconds=30)
    lease = ExecutionLease(
        job_id=message.job_id,
        event_id=message.event_id,
        attempt=message.attempt,
        lease_token=uuid4().hex,
        lease_expires_at=now + lease_duration,
    )

    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = str(message.job_id)
    session.execute.return_value = update_result

    repository = SqlAlchemyJobExecutionRepository(session)

    result = await repository.refresh_heartbeat(
        lease,
        now=now,
        lease_duration=lease_duration,
    )

    assert result == ExecutionLease(
        job_id=lease.job_id,
        event_id=lease.event_id,
        attempt=lease.attempt,
        lease_token=lease.lease_token,
        lease_expires_at=now + lease_duration,
    )

    heartbeat_statement = session.execute.await_args.args[0]
    heartbeat_sql = str(heartbeat_statement)
    where_sql = heartbeat_sql.partition(" WHERE ")[2]

    assert "ai_job.id" in where_sql
    assert "ai_job.attempt_count" in where_sql
    assert "ai_job.lease_token" in where_sql
    assert "ai_job.status" in where_sql
    assert "ai_job.lease_expires_at" in where_sql


@pytest.mark.asyncio
async def test_lost_or_expired_lease_cannot_refresh_heartbeat() -> None:
    message = build_message()
    now = datetime.now(UTC)
    lease = ExecutionLease(
        job_id=message.job_id,
        event_id=message.event_id,
        attempt=message.attempt,
        lease_token=uuid4().hex,
        lease_expires_at=now,
    )

    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = None
    session.execute.return_value = update_result

    repository = SqlAlchemyJobExecutionRepository(session)

    result = await repository.refresh_heartbeat(
        lease,
        now=now,
        lease_duration=timedelta(seconds=30),
    )

    assert result is None
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_lease_completes_job_and_attempt() -> None:
    message = build_message()
    completed_at = datetime.now(UTC)
    lease = ExecutionLease(
        job_id=message.job_id,
        event_id=message.event_id,
        attempt=message.attempt,
        lease_token=uuid4().hex,
        lease_expires_at=completed_at + timedelta(seconds=30),
    )

    session = AsyncMock(spec=AsyncSession)

    job_update_result = MagicMock()
    job_update_result.scalar_one_or_none.return_value = str(message.job_id)

    attempt_update_result = MagicMock()
    attempt_update_result.scalar_one_or_none.return_value = message.attempt

    session.execute.side_effect = [
        job_update_result,
        attempt_update_result,
    ]

    repository = SqlAlchemyJobExecutionRepository(session)

    result = await repository.complete_execution(
        lease,
        completed_at=completed_at,
    )

    assert result is True
    assert session.execute.await_count == 2

    completion_statement = session.execute.await_args_list[0].args[0]
    completion_sql = str(completion_statement)
    where_sql = completion_sql.partition(" WHERE ")[2]

    assert "ai_job.id" in where_sql
    assert "ai_job.attempt_count" in where_sql
    assert "ai_job.lease_token" in where_sql
    assert "ai_job.status" in where_sql
    assert "ai_job.lease_expires_at" in where_sql


@pytest.mark.asyncio
async def test_lost_lease_cannot_complete_job_or_attempt() -> None:
    message = build_message()
    completed_at = datetime.now(UTC)
    stale_lease = ExecutionLease(
        job_id=message.job_id,
        event_id=message.event_id,
        attempt=message.attempt,
        lease_token=uuid4().hex,
        lease_expires_at=completed_at + timedelta(seconds=30),
    )

    session = AsyncMock(spec=AsyncSession)
    job_update_result = MagicMock()
    job_update_result.scalar_one_or_none.return_value = None
    session.execute.return_value = job_update_result

    repository = SqlAlchemyJobExecutionRepository(session)

    result = await repository.complete_execution(
        stale_lease,
        completed_at=completed_at,
    )

    assert result is False
    session.execute.assert_awaited_once()
