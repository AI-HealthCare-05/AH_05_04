from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ai_worker.core.quarantine import (
    QuarantineFailureCode,
    RejectedWorkerDelivery,
)
from ai_worker.core.reconciler import (
    PendingMessageReconciler,
    ReconciliationReport,
)
from ai_worker.core.recovery import (
    ExpiredExecution,
    RecoveryDisposition,
    ScheduledRetry,
)
from ai_worker.core.stream import AutoClaimResult, WorkerDelivery
from ai_worker.schemas.messages import WorkerMessage


def build_delivery(now: datetime) -> WorkerDelivery:
    message = WorkerMessage.model_validate(
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
    return WorkerDelivery(
        stream_message_id="1000-0",
        message=message,
    )


@pytest.mark.asyncio
async def test_reconciler_recovers_schedules_commits_then_reclaims() -> None:
    now = datetime.now(UTC)
    expired = ExpiredExecution(
        job_id=uuid4(),
        event_id=uuid4(),
        attempt=1,
        max_attempts=3,
        lease_expires_at=now - timedelta(seconds=1),
    )
    scheduled = ScheduledRetry(
        job_id=expired.job_id,
        event_id=uuid4(),
        attempt=2,
        available_at=now,
    )
    delivery = build_delivery(now)
    events: list[str] = []

    repository = SimpleNamespace(
        list_expired_executions=AsyncMock(return_value=(expired,)),
        recover_expired_execution=AsyncMock(return_value=RecoveryDisposition.RETRY_WAIT),
        schedule_due_retries=AsyncMock(return_value=(scheduled,)),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: events.append("commit")),
        rollback=AsyncMock(side_effect=lambda: events.append("rollback")),
    )

    def auto_claim(**_: object) -> AutoClaimResult:
        events.append("auto_claim")
        return AutoClaimResult(
            next_start_id="0-0",
            deliveries=(delivery,),
        )

    stream = SimpleNamespace(auto_claim=AsyncMock(side_effect=auto_claim))

    executor = SimpleNamespace(execute=AsyncMock(side_effect=lambda _: events.append("execute")))

    reconciler = PendingMessageReconciler(
        repository=repository,
        transaction=transaction,
        stream=stream,
        executor=executor,
        consumer_name="reconciler-1",
        min_idle_ms=30_000,
        batch_size=100,
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    result = await reconciler.run_once()

    assert result == ReconciliationReport(
        expired_scanned=1,
        recovered_retry_wait=1,
        recovered_failed=0,
        retries_scheduled=1,
        reclaimed=1,
        next_start_id="0-0",
    )

    repository.list_expired_executions.assert_awaited_once_with(
        now=now,
        limit=100,
    )
    repository.recover_expired_execution.assert_awaited_once_with(
        expired,
        now=now,
        retry_at=now + timedelta(seconds=5),
        failure_code="TIMEOUT",
    )
    repository.schedule_due_retries.assert_awaited_once_with(
        now=now,
        limit=100,
    )
    stream.auto_claim.assert_awaited_once_with(
        consumer_name="reconciler-1",
        min_idle_ms=30_000,
        start_id="0-0",
        count=100,
    )
    executor.execute.assert_awaited_once_with(delivery)

    assert events == [
        "commit",
        "auto_claim",
        "execute",
    ]
    transaction.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_marks_exhausted_execution_failed() -> None:
    now = datetime.now(UTC)
    expired = ExpiredExecution(
        job_id=uuid4(),
        event_id=uuid4(),
        attempt=3,
        max_attempts=3,
        lease_expires_at=now - timedelta(seconds=1),
    )

    repository = SimpleNamespace(
        list_expired_executions=AsyncMock(return_value=(expired,)),
        recover_expired_execution=AsyncMock(return_value=RecoveryDisposition.FAILED),
        schedule_due_retries=AsyncMock(return_value=()),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    stream = SimpleNamespace(
        auto_claim=AsyncMock(
            return_value=AutoClaimResult(
                next_start_id="0-0",
                deliveries=(),
            )
        )
    )
    executor = SimpleNamespace(execute=AsyncMock())

    reconciler = PendingMessageReconciler(
        repository=repository,
        transaction=transaction,
        stream=stream,
        executor=executor,
        consumer_name="reconciler-1",
        min_idle_ms=30_000,
        batch_size=100,
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    result = await reconciler.run_once()

    assert result.recovered_failed == 1
    assert result.recovered_retry_wait == 0
    assert result.not_recovered == 0

    repository.recover_expired_execution.assert_awaited_once_with(
        expired,
        now=now,
        retry_at=now,
        failure_code="TIMEOUT",
    )
    transaction.commit.assert_awaited_once()
    transaction.rollback.assert_not_awaited()
    executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_rolls_back_before_reclaim_on_db_failure() -> None:
    now = datetime.now(UTC)

    repository = SimpleNamespace(
        list_expired_executions=AsyncMock(side_effect=RuntimeError("synthetic database failure")),
        recover_expired_execution=AsyncMock(),
        schedule_due_retries=AsyncMock(),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    stream = SimpleNamespace(auto_claim=AsyncMock())
    executor = SimpleNamespace(execute=AsyncMock())

    reconciler = PendingMessageReconciler(
        repository=repository,
        transaction=transaction,
        stream=stream,
        executor=executor,
        consumer_name="reconciler-1",
        min_idle_ms=30_000,
        batch_size=100,
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    with pytest.raises(RuntimeError, match="synthetic database failure"):
        await reconciler.run_once()

    transaction.commit.assert_not_awaited()
    transaction.rollback.assert_awaited_once()
    stream.auto_claim.assert_not_awaited()
    executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciler_continues_from_auto_claim_cursor() -> None:
    now = datetime.now(UTC)

    repository = SimpleNamespace(
        list_expired_executions=AsyncMock(return_value=()),
        recover_expired_execution=AsyncMock(),
        schedule_due_retries=AsyncMock(return_value=()),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    stream = SimpleNamespace(
        auto_claim=AsyncMock(
            side_effect=[
                AutoClaimResult(
                    next_start_id="2000-0",
                    deliveries=(),
                ),
                AutoClaimResult(
                    next_start_id="0-0",
                    deliveries=(),
                ),
            ]
        )
    )
    executor = SimpleNamespace(execute=AsyncMock())

    reconciler = PendingMessageReconciler(
        repository=repository,
        transaction=transaction,
        stream=stream,
        executor=executor,
        consumer_name="reconciler-1",
        min_idle_ms=30_000,
        batch_size=100,
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    first = await reconciler.run_once()
    second = await reconciler.run_once()

    assert first.next_start_id == "2000-0"
    assert second.next_start_id == "0-0"

    assert stream.auto_claim.await_args_list[0].kwargs["start_id"] == "0-0"
    assert stream.auto_claim.await_args_list[1].kwargs["start_id"] == "2000-0"
    assert transaction.commit.await_count == 2


@pytest.mark.asyncio
async def test_reconciler_routes_rejected_pending_delivery_to_quarantine() -> None:
    now = datetime.now(UTC)
    rejected = RejectedWorkerDelivery(
        stream_name="oryak:jobs",
        stream_entry_id="1007-0",
        message_digest="a" * 64,
        failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
        job_id=None,
        original_event_id=None,
        original_schema_version="1.0",
        trace_id=None,
    )
    repository = SimpleNamespace(
        list_expired_executions=AsyncMock(return_value=()),
        recover_expired_execution=AsyncMock(),
        schedule_due_retries=AsyncMock(return_value=()),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    stream = SimpleNamespace(
        auto_claim=AsyncMock(
            return_value=AutoClaimResult(
                next_start_id="0-0",
                deliveries=(rejected,),
            )
        )
    )
    executor = SimpleNamespace(execute=AsyncMock())
    rejected_executor = SimpleNamespace(execute=AsyncMock())

    reconciler = PendingMessageReconciler(
        repository=repository,
        transaction=transaction,
        stream=stream,
        executor=executor,
        rejected_executor=rejected_executor,
        consumer_name="reconciler-1",
        min_idle_ms=30_000,
        batch_size=100,
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    report = await reconciler.run_once()

    assert report.reclaimed == 1
    executor.execute.assert_not_awaited()
    rejected_executor.execute.assert_awaited_once_with(rejected)
