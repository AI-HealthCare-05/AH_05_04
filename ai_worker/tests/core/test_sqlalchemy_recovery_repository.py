"""만료된 Worker 실행 복구 Repository 테스트입니다."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_recovery_repository import (
    SqlAlchemyRecoveryRepository,
)
from ai_worker.core.recovery import (
    ExpiredExecution,
    RecoveryDisposition,
)


def build_expired_execution(
    *,
    attempt: int = 1,
    max_attempts: int = 3,
) -> ExpiredExecution:
    now = datetime.now(UTC)

    return ExpiredExecution(
        job_id=uuid4(),
        event_id=uuid4(),
        attempt=attempt,
        max_attempts=max_attempts,
        lease_expires_at=now - timedelta(seconds=1),
    )


@pytest.mark.asyncio
async def test_expired_execution_moves_to_retry_wait() -> None:
    execution = build_expired_execution(
        attempt=1,
        max_attempts=3,
    )
    now = datetime.now(UTC)
    retry_at = now + timedelta(seconds=5)

    session = AsyncMock(spec=AsyncSession)

    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = "OCR"

    attempt_result = MagicMock()
    attempt_result.scalar_one_or_none.return_value = execution.attempt

    session.execute.side_effect = [
        job_result,
        attempt_result,
    ]

    repository = SqlAlchemyRecoveryRepository(session)

    result = await repository.recover_expired_execution(
        execution,
        now=now,
        retry_at=retry_at,
        failure_code="DEPENDENCY_UNAVAILABLE",
    )

    assert result is RecoveryDisposition.RETRY_WAIT
    assert session.execute.await_count == 2

    job_statement = session.execute.await_args_list[0].args[0]
    job_sql = str(job_statement)
    job_params = job_statement.compile().params
    where_sql = job_sql.partition(" WHERE ")[2]

    assert "UPDATE ai_job SET" in job_sql
    assert "ai_job.id" in where_sql
    assert "ai_job.status" in where_sql
    assert "ai_job.expected_event_id" in where_sql
    assert "ai_job.attempt_count" in where_sql
    assert "ai_job.lease_expires_at" in where_sql

    assert "RETRY_WAIT" in job_params.values()
    assert str(execution.event_id) in job_params.values()
    assert retry_at in job_params.values()

    attempt_statement = session.execute.await_args_list[1].args[0]
    attempt_sql = str(attempt_statement)
    attempt_params = attempt_statement.compile().params
    attempt_where_sql = attempt_sql.partition(" WHERE ")[2]

    assert "UPDATE ai_job_attempt SET" in attempt_sql
    assert "ai_job_attempt.ai_job_id" in attempt_where_sql
    assert "ai_job_attempt.attempt_no" in attempt_where_sql
    assert "ai_job_attempt.attempt_status" in attempt_where_sql

    assert "FAILED" in attempt_params.values()
    assert "DEPENDENCY_UNAVAILABLE" in attempt_params.values()
    assert True in attempt_params.values()


@pytest.mark.asyncio
async def test_last_attempt_moves_job_to_failed() -> None:
    execution = build_expired_execution(
        attempt=3,
        max_attempts=3,
    )
    now = datetime.now(UTC)

    session = AsyncMock(spec=AsyncSession)

    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = "OCR"

    attempt_result = MagicMock()
    attempt_result.scalar_one_or_none.return_value = execution.attempt

    ocr_result = MagicMock()
    ocr_result.scalar_one_or_none.return_value = "ocr-job-id"

    session.execute.side_effect = [
        job_result,
        attempt_result,
        ocr_result,
    ]

    repository = SqlAlchemyRecoveryRepository(session)

    result = await repository.recover_expired_execution(
        execution,
        now=now,
        retry_at=now,
        failure_code="DEPENDENCY_UNAVAILABLE",
    )

    assert result is RecoveryDisposition.FAILED
    assert session.execute.await_count == 3

    job_statement = session.execute.await_args_list[0].args[0]
    job_params = job_statement.compile().params

    assert "FAILED" in job_params.values()
    assert "RETRY_EXHAUSTED" in job_params.values()
    assert now in job_params.values()

    attempt_statement = session.execute.await_args_list[1].args[0]
    attempt_params = attempt_statement.compile().params

    assert "FAILED" in attempt_params.values()
    assert "DEPENDENCY_UNAVAILABLE" in attempt_params.values()
    assert False in attempt_params.values()

    ocr_statement = session.execute.await_args_list[2].args[0]
    ocr_params = ocr_statement.compile().params

    assert "UPDATE ocr_job SET" in str(ocr_statement)
    assert "FAILED" in ocr_params.values()
    assert "OCR_PROVIDER_UNAVAILABLE" in ocr_params.values()


@pytest.mark.asyncio
async def test_unexpired_or_already_recovered_execution_is_not_changed() -> None:
    execution = build_expired_execution()
    now = datetime.now(UTC)

    session = AsyncMock(spec=AsyncSession)

    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = None
    session.execute.return_value = job_result

    repository = SqlAlchemyRecoveryRepository(session)

    result = await repository.recover_expired_execution(
        execution,
        now=now,
        retry_at=now + timedelta(seconds=5),
        failure_code="DEPENDENCY_UNAVAILABLE",
    )

    assert result is RecoveryDisposition.NOT_RECOVERED

    # Job 조건부 갱신이 실패하면 Attempt는 건드리지 않습니다.
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_requires_matching_current_event_and_attempt() -> None:
    execution = build_expired_execution()
    now = datetime.now(UTC)

    session = AsyncMock(spec=AsyncSession)
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = None
    session.execute.return_value = job_result

    repository = SqlAlchemyRecoveryRepository(session)

    await repository.recover_expired_execution(
        execution,
        now=now,
        retry_at=now + timedelta(seconds=5),
        failure_code="DEPENDENCY_UNAVAILABLE",
    )

    statement = session.execute.await_args.args[0]
    where_sql = str(statement).partition(" WHERE ")[2]

    assert "ai_job.id" in where_sql
    assert "ai_job.expected_event_id" in where_sql
    assert "ai_job.attempt_count" in where_sql
    assert "ai_job.status" in where_sql
    assert "ai_job.lease_expires_at" in where_sql


@pytest.mark.asyncio
async def test_due_retry_creates_one_next_attempt_outbox() -> None:
    job_id = uuid4()
    domain_id = uuid4()
    trace_id = uuid4().hex
    now = datetime.now(UTC)

    session = AsyncMock(spec=AsyncSession)

    candidate_result = MagicMock()
    candidate_result.mappings.return_value.all.return_value = [
        {
            "id": str(job_id),
            "attempt_count": 1,
            "max_attempts": 3,
            "available_at": now,
            "trace_id": trace_id,
            "domain_type": "OCR_JOB",
            "domain_id": str(domain_id),
        }
    ]

    insert_result = MagicMock()

    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = str(job_id)

    session.execute.side_effect = [
        candidate_result,
        insert_result,
        update_result,
    ]

    repository = SqlAlchemyRecoveryRepository(session)

    scheduled = await repository.schedule_due_retries(
        now=now,
        limit=10,
    )

    assert len(scheduled) == 1
    assert scheduled[0].job_id == job_id
    assert scheduled[0].attempt == 2
    assert scheduled[0].available_at == now
    assert session.execute.await_count == 3

    select_statement = session.execute.await_args_list[0].args[0]
    select_sql = str(select_statement)

    assert "WHERE" in select_sql
    assert "ai_job.status" in select_sql
    assert "ai_job.available_at" in select_sql
    assert "ai_job.expected_event_id" in select_sql
    assert "ai_job.attempt_count" in select_sql
    assert "ai_job.max_attempts" in select_sql
    assert "ai_job.last_consumed_event_id" in select_sql
    assert "outbox_event.event_id" in select_sql
    assert "outbox_event.trace_id" in select_sql
    assert "outbox_event.domain_type" in select_sql
    assert "outbox_event.domain_id" in select_sql
    assert "FOR UPDATE" in select_sql

    insert_statement = session.execute.await_args_list[1].args[0]
    insert_sql = str(insert_statement)
    insert_params = insert_statement.compile().params

    assert "INSERT INTO outbox_event" in insert_sql
    assert str(job_id) in insert_params.values()
    assert 2 in insert_params.values()
    assert "JOB_EXECUTE" in insert_params.values()
    assert "PENDING" in insert_params.values()
    assert trace_id in insert_params.values()
    assert "OCR_JOB" in insert_params.values()
    assert str(domain_id) in insert_params.values()

    update_statement = session.execute.await_args_list[2].args[0]
    update_sql = str(update_statement)
    update_where_sql = update_sql.partition(" WHERE ")[2]
    update_params = update_statement.compile().params

    assert "UPDATE ai_job SET" in update_sql
    assert "ai_job.id" in update_where_sql
    assert "ai_job.status" in update_where_sql
    assert "ai_job.expected_event_id" in update_where_sql
    assert "ai_job.attempt_count" in update_where_sql
    assert str(scheduled[0].event_id) in update_params.values()


@pytest.mark.asyncio
async def test_retry_not_due_does_not_create_outbox() -> None:
    session = AsyncMock(spec=AsyncSession)

    candidate_result = MagicMock()
    candidate_result.mappings.return_value.all.return_value = []
    session.execute.return_value = candidate_result

    repository = SqlAlchemyRecoveryRepository(session)

    scheduled = await repository.schedule_due_retries(
        now=datetime.now(UTC),
        limit=10,
    )

    assert scheduled == ()
    session.execute.assert_awaited_once()
