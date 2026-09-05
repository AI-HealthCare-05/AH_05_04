"""Pending Reconciler와 DLQ Publisher 주기 실행 테스트입니다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_worker.core.recovery_scheduler import RecoveryScheduler


@pytest.mark.asyncio
async def test_scheduler_runs_reconciler_and_dlq_publisher_independently() -> None:
    reconciler_called = asyncio.Event()
    dlq_publisher_called = asyncio.Event()
    stop_event = asyncio.Event()

    async def run_reconciler() -> None:
        reconciler_called.set()

    async def run_dlq_publisher() -> None:
        dlq_publisher_called.set()

    reconciler = SimpleNamespace(run_once=AsyncMock(side_effect=run_reconciler))
    dlq_publisher = SimpleNamespace(run_once=AsyncMock(side_effect=run_dlq_publisher))
    failure_reporter = SimpleNamespace(report_failure=AsyncMock())

    scheduler = RecoveryScheduler(
        reconciler=reconciler,
        dlq_publisher=dlq_publisher,
        failure_reporter=failure_reporter,
        reconciler_interval_seconds=5.0,
        dlq_publisher_interval_seconds=1.0,
    )

    scheduler_task = asyncio.create_task(scheduler.run(stop_event=stop_event))

    await asyncio.wait_for(
        asyncio.gather(
            reconciler_called.wait(),
            dlq_publisher_called.wait(),
        ),
        timeout=1,
    )

    stop_event.set()
    await asyncio.wait_for(scheduler_task, timeout=1)

    reconciler.run_once.assert_awaited_once()
    dlq_publisher.run_once.assert_awaited_once()
    failure_reporter.report_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_reports_failure_and_continues_other_cycles() -> None:
    reconciler_recovered = asyncio.Event()
    dlq_publisher_called = asyncio.Event()
    stop_event = asyncio.Event()
    reconciler_attempt = 0

    async def run_reconciler() -> None:
        nonlocal reconciler_attempt
        reconciler_attempt += 1

        if reconciler_attempt == 1:
            raise RuntimeError("synthetic sensitive detail must not be reported")

        reconciler_recovered.set()

    async def run_dlq_publisher() -> None:
        dlq_publisher_called.set()

    reconciler = SimpleNamespace(run_once=AsyncMock(side_effect=run_reconciler))
    dlq_publisher = SimpleNamespace(run_once=AsyncMock(side_effect=run_dlq_publisher))
    failure_reporter = SimpleNamespace(report_failure=AsyncMock())

    scheduler = RecoveryScheduler(
        reconciler=reconciler,
        dlq_publisher=dlq_publisher,
        failure_reporter=failure_reporter,
        reconciler_interval_seconds=0.001,
        dlq_publisher_interval_seconds=0.001,
    )

    scheduler_task = asyncio.create_task(scheduler.run(stop_event=stop_event))

    await asyncio.wait_for(
        asyncio.gather(
            reconciler_recovered.wait(),
            dlq_publisher_called.wait(),
        ),
        timeout=1,
    )

    stop_event.set()
    await asyncio.wait_for(scheduler_task, timeout=1)

    assert reconciler.run_once.await_count >= 2
    assert dlq_publisher.run_once.await_count >= 1

    failure_reporter.report_failure.assert_awaited_once_with(
        task_name="pending_reconciler",
    )


@pytest.mark.asyncio
async def test_scheduler_does_not_run_when_already_stopped() -> None:
    stop_event = asyncio.Event()
    stop_event.set()

    reconciler = SimpleNamespace(run_once=AsyncMock())
    dlq_publisher = SimpleNamespace(run_once=AsyncMock())
    failure_reporter = SimpleNamespace(report_failure=AsyncMock())

    scheduler = RecoveryScheduler(
        reconciler=reconciler,
        dlq_publisher=dlq_publisher,
        failure_reporter=failure_reporter,
        reconciler_interval_seconds=5.0,
        dlq_publisher_interval_seconds=1.0,
    )

    await scheduler.run(stop_event=stop_event)

    reconciler.run_once.assert_not_awaited()
    dlq_publisher.run_once.assert_not_awaited()
    failure_reporter.report_failure.assert_not_awaited()


@pytest.mark.parametrize(
    (
        "reconciler_interval_seconds",
        "dlq_publisher_interval_seconds",
    ),
    [
        (0, 1.0),
        (-1, 1.0),
        (5.0, 0),
        (5.0, -1),
    ],
)
def test_scheduler_rejects_non_positive_intervals(
    reconciler_interval_seconds: float,
    dlq_publisher_interval_seconds: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="interval",
    ):
        RecoveryScheduler(
            reconciler=SimpleNamespace(run_once=AsyncMock()),
            dlq_publisher=SimpleNamespace(run_once=AsyncMock()),
            failure_reporter=SimpleNamespace(report_failure=AsyncMock()),
            reconciler_interval_seconds=(reconciler_interval_seconds),
            dlq_publisher_interval_seconds=(dlq_publisher_interval_seconds),
        )
