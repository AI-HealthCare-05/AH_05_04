import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.commands import process_notifications as command
from app.commands import schedule_notifications as scheduler
from app.services.notifications import NotificationBatchResult


async def test_success_logs_counts_and_last_success_in_rendered_message(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(command, "default_logger", logger)
    monkeypatch.setattr(command, "process_notifications_once", AsyncMock(return_value=NotificationBatchResult(3, 2, 1)))
    close = AsyncMock()
    monkeypatch.setattr(command, "close_database", close)
    assert await command.run() is True
    args = logger.info.call_args.args
    output = args[0] % args[1:]
    assert "status=success completed_at=" in output
    assert "created_count=3 delivered_count=2 cancelled_count=1" in output
    close.assert_awaited_once()


@pytest.mark.parametrize(
    "failure", [RuntimeError("SYNTHETIC_PRIVATE_SENTINEL"), TimeoutError("SYNTHETIC_PRIVATE_SENTINEL")]
)
async def test_failure_logs_only_fixed_reason_and_closes_database(monkeypatch, failure):
    logger = Mock()
    monkeypatch.setattr(command, "default_logger", logger)
    monkeypatch.setattr(command, "process_notifications_once", AsyncMock(side_effect=failure))
    close = AsyncMock()
    monkeypatch.setattr(command, "close_database", close)
    assert await command.run() is False
    args = logger.error.call_args.args
    output = args[0] % args[1:]
    assert "status=failed" in output and "SYNTHETIC_PRIVATE_SENTINEL" not in output
    assert "count=" not in output
    assert logger.error.call_args.kwargs == {}
    close.assert_awaited_once()


async def test_periodic_runtime_waits_after_failure_then_runs_again_without_overlap(monkeypatch):
    calls = []

    async def run():
        calls.append("run")
        return len(calls) > 1

    async def sleep(seconds):
        assert seconds == 60
        calls.append("wait")
        if len(calls) == 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "run", run)
    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.serve()
    assert calls == ["run", "wait", "run", "wait"]


async def test_cancelled_batch_closes_database_and_is_not_reported_as_success(monkeypatch):
    close = AsyncMock()
    monkeypatch.setattr(command, "close_database", close)
    monkeypatch.setattr(command, "process_notifications_once", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await command.run()
    close.assert_awaited_once()


@pytest.mark.parametrize("success,code", [(True, 0), (False, 1)])
def test_one_shot_exit_status(monkeypatch, success, code):
    monkeypatch.setattr(command, "run", AsyncMock(return_value=success))
    with pytest.raises(SystemExit) as result:
        command.main()
    assert result.value.code == code


async def test_sigterm_cancels_active_cycle_and_removes_handlers(monkeypatch):
    import signal

    loop = asyncio.get_running_loop()
    handlers = {}
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def serve():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    monkeypatch.setattr(scheduler, "serve", serve)
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, callback: handlers.__setitem__(sig, callback))
    remove = Mock()
    monkeypatch.setattr(loop, "remove_signal_handler", remove)
    task = asyncio.create_task(scheduler.run_scheduler())
    await entered.wait()
    handlers[signal.SIGTERM]()
    await asyncio.wait_for(task, timeout=1)
    assert finished.is_set()
    assert remove.call_count == 2
