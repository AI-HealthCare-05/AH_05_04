"""Pending Reconciler와 DLQ Publisher 주기 실행 테스트입니다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_worker.core import recovery_scheduler as scheduler_module
from ai_worker.core.recovery_scheduler import RecoveryScheduler


@pytest.mark.asyncio
async def test_scheduler_runs_outbox_reconciler_and_dlq_publisher_independently() -> None:
    outbox_publisher_called = asyncio.Event()
    reconciler_called = asyncio.Event()
    dlq_publisher_called = asyncio.Event()
    stop_event = asyncio.Event()

    async def run_outbox_publisher() -> None:
        outbox_publisher_called.set()

    async def run_reconciler() -> None:
        reconciler_called.set()

    async def run_dlq_publisher() -> None:
        dlq_publisher_called.set()

    outbox_publisher = SimpleNamespace(run_once=AsyncMock(side_effect=run_outbox_publisher))
    reconciler = SimpleNamespace(run_once=AsyncMock(side_effect=run_reconciler))
    dlq_publisher = SimpleNamespace(run_once=AsyncMock(side_effect=run_dlq_publisher))
    failure_reporter = SimpleNamespace(report_failure=AsyncMock())

    scheduler = RecoveryScheduler(
        outbox_publisher=outbox_publisher,
        reconciler=reconciler,
        dlq_publisher=dlq_publisher,
        failure_reporter=failure_reporter,
        outbox_publisher_interval_seconds=1.0,
        reconciler_interval_seconds=5.0,
        dlq_publisher_interval_seconds=1.0,
    )

    scheduler_task = asyncio.create_task(scheduler.run(stop_event=stop_event))

    await asyncio.wait_for(
        asyncio.gather(
            outbox_publisher_called.wait(),
            reconciler_called.wait(),
            dlq_publisher_called.wait(),
        ),
        timeout=1,
    )

    stop_event.set()
    await asyncio.wait_for(scheduler_task, timeout=1)

    outbox_publisher.run_once.assert_awaited_once()
    reconciler.run_once.assert_awaited_once()
    dlq_publisher.run_once.assert_awaited_once()
    failure_reporter.report_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_reports_outbox_failure_and_retries_without_stopping_other_cycles() -> None:
    outbox_publisher_recovered = asyncio.Event()
    reconciler_called = asyncio.Event()
    dlq_publisher_called = asyncio.Event()
    stop_event = asyncio.Event()
    outbox_attempt = 0

    async def run_outbox_publisher() -> None:
        nonlocal outbox_attempt
        outbox_attempt += 1

        if outbox_attempt == 1:
            raise RuntimeError("synthetic sensitive detail must not be reported")

        outbox_publisher_recovered.set()

    async def run_reconciler() -> None:
        reconciler_called.set()

    async def run_dlq_publisher() -> None:
        dlq_publisher_called.set()

    outbox_publisher = SimpleNamespace(run_once=AsyncMock(side_effect=run_outbox_publisher))
    reconciler = SimpleNamespace(run_once=AsyncMock(side_effect=run_reconciler))
    dlq_publisher = SimpleNamespace(run_once=AsyncMock(side_effect=run_dlq_publisher))
    failure_reporter = SimpleNamespace(report_failure=AsyncMock())

    scheduler = RecoveryScheduler(
        outbox_publisher=outbox_publisher,
        reconciler=reconciler,
        dlq_publisher=dlq_publisher,
        failure_reporter=failure_reporter,
        outbox_publisher_interval_seconds=0.001,
        reconciler_interval_seconds=0.001,
        dlq_publisher_interval_seconds=0.001,
    )

    scheduler_task = asyncio.create_task(scheduler.run(stop_event=stop_event))

    await asyncio.wait_for(
        asyncio.gather(
            outbox_publisher_recovered.wait(),
            reconciler_called.wait(),
            dlq_publisher_called.wait(),
        ),
        timeout=1,
    )

    stop_event.set()
    await asyncio.wait_for(scheduler_task, timeout=1)

    assert outbox_publisher.run_once.await_count >= 2
    assert reconciler.run_once.await_count >= 1
    assert dlq_publisher.run_once.await_count >= 1

    failure_reporter.report_failure.assert_awaited_once_with(
        task_name="outbox_publisher",
    )


@pytest.mark.asyncio
async def test_outbox_publisher_repeats_at_configured_interval_with_fake_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = asyncio.Event()
    publisher = SimpleNamespace(run_once=AsyncMock())
    observed_intervals: list[float] = []

    async def fake_wait_for_next_cycle(*, stop_event: asyncio.Event, interval_seconds: float) -> None:
        observed_intervals.append(interval_seconds)
        if len(observed_intervals) == 2:
            stop_event.set()

    monkeypatch.setattr(scheduler_module, "_wait_for_next_cycle", fake_wait_for_next_cycle)
    scheduler = RecoveryScheduler(
        outbox_publisher=publisher,
        reconciler=SimpleNamespace(run_once=AsyncMock()),
        dlq_publisher=SimpleNamespace(run_once=AsyncMock()),
        failure_reporter=SimpleNamespace(report_failure=AsyncMock()),
        outbox_publisher_interval_seconds=0.25,
        reconciler_interval_seconds=5.0,
        dlq_publisher_interval_seconds=1.0,
    )

    await scheduler._run_periodically(
        task=publisher,
        task_name="outbox_publisher",
        interval_seconds=0.25,
        stop_event=stop_event,
    )

    assert publisher.run_once.await_count == 2
    assert observed_intervals == [0.25, 0.25]


@pytest.mark.asyncio
async def test_scheduler_does_not_run_when_already_stopped() -> None:
    stop_event = asyncio.Event()
    stop_event.set()

    outbox_publisher = SimpleNamespace(run_once=AsyncMock())
    reconciler = SimpleNamespace(run_once=AsyncMock())
    dlq_publisher = SimpleNamespace(run_once=AsyncMock())
    failure_reporter = SimpleNamespace(report_failure=AsyncMock())

    scheduler = RecoveryScheduler(
        outbox_publisher=outbox_publisher,
        reconciler=reconciler,
        dlq_publisher=dlq_publisher,
        failure_reporter=failure_reporter,
        outbox_publisher_interval_seconds=1.0,
        reconciler_interval_seconds=5.0,
        dlq_publisher_interval_seconds=1.0,
    )

    await scheduler.run(stop_event=stop_event)

    outbox_publisher.run_once.assert_not_awaited()
    reconciler.run_once.assert_not_awaited()
    dlq_publisher.run_once.assert_not_awaited()
    failure_reporter.report_failure.assert_not_awaited()


@pytest.mark.parametrize(
    (
        "reconciler_interval_seconds",
        "dlq_publisher_interval_seconds",
        "outbox_publisher_interval_seconds",
    ),
    [
        (0, 1.0, 1.0),
        (-1, 1.0, 1.0),
        (5.0, 0, 1.0),
        (5.0, -1, 1.0),
        (5.0, 1.0, 0),
        (5.0, 1.0, -1),
    ],
)
def test_scheduler_rejects_non_positive_intervals(
    reconciler_interval_seconds: float,
    dlq_publisher_interval_seconds: float,
    outbox_publisher_interval_seconds: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="interval",
    ):
        RecoveryScheduler(
            outbox_publisher=SimpleNamespace(run_once=AsyncMock()),
            reconciler=SimpleNamespace(run_once=AsyncMock()),
            dlq_publisher=SimpleNamespace(run_once=AsyncMock()),
            failure_reporter=SimpleNamespace(report_failure=AsyncMock()),
            outbox_publisher_interval_seconds=outbox_publisher_interval_seconds,
            reconciler_interval_seconds=(reconciler_interval_seconds),
            dlq_publisher_interval_seconds=(dlq_publisher_interval_seconds),
        )
