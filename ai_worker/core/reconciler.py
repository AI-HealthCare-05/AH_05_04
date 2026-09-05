"""만료 실행 복구, 재시도 예약, Pending reclaim을 조정합니다."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ai_worker.core.consumer_execution import Transaction
from ai_worker.core.consumer_runtime import RejectedDeliveryExecution
from ai_worker.core.quarantine import RejectedWorkerDelivery
from ai_worker.core.recovery import (
    RecoveryDisposition,
    RecoveryRepository,
)
from ai_worker.core.retry import calculate_retry_decision
from ai_worker.core.stream import StreamConsumer, WorkerDelivery


class ReclaimedDeliveryExecutor(Protocol):
    async def execute(self, delivery: WorkerDelivery) -> object:
        """회수한 메시지를 기존 lease·fencing 실행 경계로 처리합니다."""
        ...


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Reconciler 한 번의 실행 결과입니다."""

    expired_scanned: int
    recovered_retry_wait: int
    recovered_failed: int
    retries_scheduled: int
    reclaimed: int
    next_start_id: str
    not_recovered: int = 0


class PendingMessageReconciler:
    """DB lease 복구를 commit한 뒤 Redis Pending 메시지를 회수합니다."""

    def __init__(
        self,
        *,
        repository: RecoveryRepository,
        transaction: Transaction,
        stream: StreamConsumer,
        executor: ReclaimedDeliveryExecutor,
        consumer_name: str,
        min_idle_ms: int,
        batch_size: int,
        clock: Callable[[], datetime],
        random_value: Callable[[], float],
        rejected_executor: RejectedDeliveryExecution | None = None,
    ) -> None:
        normalized_consumer_name = consumer_name.strip()

        if not normalized_consumer_name:
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if min_idle_ms < 0:
            raise ValueError("min_idle_ms는 0 이상이어야 합니다.")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise TypeError("batch_size는 정수여야 합니다.")
        if batch_size < 1:
            raise ValueError("batch_size는 1 이상이어야 합니다.")

        self._repository = repository
        self._transaction = transaction
        self._stream = stream
        self._executor = executor
        self._consumer_name = normalized_consumer_name
        self._min_idle_ms = min_idle_ms
        self._batch_size = batch_size
        self._clock = clock
        self._random_value = random_value
        self._next_start_id = "0-0"
        self._rejected_executor = rejected_executor

    async def run_once(self) -> ReconciliationReport:
        """만료 lease와 due retry를 저장한 뒤 Pending entry를 회수합니다."""

        now = self._clock()
        recovered_retry_wait = 0
        recovered_failed = 0
        not_recovered = 0

        try:
            expired_executions = await self._repository.list_expired_executions(
                now=now,
                limit=self._batch_size,
            )

            for execution in expired_executions:
                decision = calculate_retry_decision(
                    attempt_count=execution.attempt,
                    max_attempts=execution.max_attempts,
                    failure_code="TIMEOUT",
                    random_value=self._random_value,
                )
                retry_at = now + timedelta(
                    seconds=decision.delay_seconds or 0,
                )

                disposition = await self._repository.recover_expired_execution(
                    execution,
                    now=now,
                    retry_at=retry_at,
                    failure_code="TIMEOUT",
                )

                if disposition is RecoveryDisposition.RETRY_WAIT:
                    recovered_retry_wait += 1
                elif disposition is RecoveryDisposition.FAILED:
                    recovered_failed += 1
                else:
                    not_recovered += 1

            scheduled_retries = await self._repository.schedule_due_retries(
                now=now,
                limit=self._batch_size,
            )

            await self._transaction.commit()
        except BaseException:
            await self._rollback_safely()
            raise

        claim_result = await self._stream.auto_claim(
            consumer_name=self._consumer_name,
            min_idle_ms=self._min_idle_ms,
            start_id=self._next_start_id,
            count=self._batch_size,
        )
        self._next_start_id = claim_result.next_start_id

        for delivery in claim_result.deliveries:
            await self._execute_reclaimed_delivery(delivery)

        return ReconciliationReport(
            expired_scanned=len(expired_executions),
            recovered_retry_wait=recovered_retry_wait,
            recovered_failed=recovered_failed,
            retries_scheduled=len(scheduled_retries),
            reclaimed=len(claim_result.deliveries),
            next_start_id=claim_result.next_start_id,
            not_recovered=not_recovered,
        )

    async def _execute_reclaimed_delivery(
        self,
        delivery: WorkerDelivery | RejectedWorkerDelivery,
    ) -> None:
        if isinstance(delivery, RejectedWorkerDelivery):
            if self._rejected_executor is None:
                raise RuntimeError("Rejected delivery 실행기가 구성되지 않았습니다.")

            await self._rejected_executor.execute(delivery)
            return

        await self._executor.execute(delivery)

    async def _rollback_safely(self) -> None:
        try:
            await self._transaction.rollback()
        except Exception:
            # rollback 오류가 원래 복구 실패를 덮어쓰지 않게 합니다.
            return
