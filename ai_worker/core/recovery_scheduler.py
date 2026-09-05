"""Pending Reconciler와 DLQ Publisher의 독립 주기 실행기입니다."""

import asyncio
from typing import Protocol


class ScheduledRecoveryTask(Protocol):
    """한 번 실행 가능한 복구 작업 계약입니다."""

    async def run_once(self) -> object:
        """복구 작업 한 cycle을 실행합니다."""
        ...


class RecoveryFailureReporter(Protocol):
    """원본 예외 내용을 포함하지 않는 복구 실패 보고 계약입니다."""

    async def report_failure(
        self,
        *,
        task_name: str,
    ) -> None:
        """안전한 작업 식별자만 사용해 실패를 보고합니다."""
        ...


class RecoveryScheduler:
    """Pending reclaim과 DLQ 발행을 독립적인 주기로 실행합니다."""

    def __init__(
        self,
        *,
        reconciler: ScheduledRecoveryTask,
        dlq_publisher: ScheduledRecoveryTask,
        failure_reporter: RecoveryFailureReporter,
        reconciler_interval_seconds: float,
        dlq_publisher_interval_seconds: float,
    ) -> None:
        _validate_interval(
            reconciler_interval_seconds,
        )
        _validate_interval(
            dlq_publisher_interval_seconds,
        )

        self._reconciler = reconciler
        self._dlq_publisher = dlq_publisher
        self._failure_reporter = failure_reporter
        self._reconciler_interval_seconds = reconciler_interval_seconds
        self._dlq_publisher_interval_seconds = dlq_publisher_interval_seconds

    async def run(
        self,
        *,
        stop_event: asyncio.Event,
    ) -> None:
        """두 복구 loop를 함께 시작하고 종료 신호까지 유지합니다."""

        if stop_event.is_set():
            return

        async with asyncio.TaskGroup() as task_group:
            task_group.create_task(
                self._run_periodically(
                    task=self._reconciler,
                    task_name="pending_reconciler",
                    interval_seconds=(self._reconciler_interval_seconds),
                    stop_event=stop_event,
                )
            )
            task_group.create_task(
                self._run_periodically(
                    task=self._dlq_publisher,
                    task_name="dlq_publisher",
                    interval_seconds=(self._dlq_publisher_interval_seconds),
                    stop_event=stop_event,
                )
            )

    async def _run_periodically(
        self,
        *,
        task: ScheduledRecoveryTask,
        task_name: str,
        interval_seconds: float,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await task.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 예외 원문에는 Redis 주소, DB 정보 또는 메시지 내용이
                # 포함될 수 있으므로 reporter에는 안전한 이름만 전달합니다.
                await self._failure_reporter.report_failure(
                    task_name=task_name,
                )

            await _wait_for_next_cycle(
                stop_event=stop_event,
                interval_seconds=interval_seconds,
            )


async def _wait_for_next_cycle(
    *,
    stop_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    try:
        await asyncio.wait_for(
            stop_event.wait(),
            timeout=interval_seconds,
        )
    except TimeoutError:
        return


def _validate_interval(interval_seconds: float) -> None:
    if isinstance(interval_seconds, bool) or not isinstance(
        interval_seconds,
        int | float,
    ):
        raise TypeError("interval은 숫자여야 합니다.")

    if interval_seconds <= 0:
        raise ValueError("interval은 0보다 커야 합니다.")
