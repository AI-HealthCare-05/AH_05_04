"""Handler 결과 검증 이후 저장·commit·ACK 순서를 조정합니다."""

import asyncio
import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import (
    ConsumerAcknowledgementError,
    ConsumerPersistenceError,
    WorkerError,
)
from ai_worker.core.handler import HandlerExecutionContext
from ai_worker.core.job_execution import (
    CommittedDelivery,
    ExecutionLease,
    JobExecutionRepository,
    LeaseHeartbeat,
    LeaseHeartbeatHandle,
    LeaseNotAcquired,
)
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.stream import StreamAcknowledger, WorkerDelivery
from ai_worker.schemas.messages import WorkerMessage


class ResultStore(Protocol):
    """검증된 Handler 결과를 저장하는 추상 인터페이스입니다."""

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        """현재 transaction에 결과를 저장하되 직접 commit하지 않습니다."""
        ...


class DomainExecutionStarter(Protocol):
    """Provider 호출 전에 도메인 상태 변경을 준비합니다."""

    async def start(
        self,
        *,
        message: WorkerMessage,
        started_at: datetime,
    ) -> bool:
        """현재 transaction에 시작 상태를 적재하되 commit하지 않습니다."""
        ...


class Transaction(Protocol):
    """Consumer가 소유하는 DB transaction 경계입니다."""

    async def commit(self) -> None:
        """현재 transaction을 commit합니다."""
        ...

    async def rollback(self) -> None:
        """현재 transaction을 rollback합니다."""
        ...


class ConsumerExecution:
    """Handler 실행부터 ACK까지의 순서를 조정합니다."""

    def __init__(
        self,
        *,
        dispatcher: Dispatcher,
        result_store: ResultStore,
        transaction: Transaction,
        acknowledger: StreamAcknowledger,
    ) -> None:
        self._dispatcher = dispatcher
        self._result_store = result_store
        self._transaction = transaction
        self._acknowledger = acknowledger

    async def execute(self, delivery: WorkerDelivery) -> HandlerSuccess:
        """검증된 결과를 저장·commit한 뒤에만 ACK합니다."""

        try:
            result = await self._dispatcher.dispatch(delivery.message)
        except (WorkerError, asyncio.CancelledError):
            # Handler가 같은 transaction에 이미 변경을 남겼을 수 있으므로
            # 결과 검증 실패도 Consumer의 정리 범위에서 rollback합니다.
            await self._rollback_safely()
            raise

        persistence_error: ConsumerPersistenceError | None = None

        try:
            await self._result_store.save(
                message=delivery.message,
                result=result,
            )
            await self._transaction.commit()
        except asyncio.CancelledError:
            # CancelledError는 BaseException이라 아래 except Exception에
            # 잡히지 않으므로 별도로 정리한 뒤 그대로 전파합니다.
            await self._rollback_safely()
            raise
        except Exception:
            await self._rollback_safely()
            persistence_error = ConsumerPersistenceError()

        if persistence_error is not None:
            # 활성 예외 처리 구간 밖에서 새 오류를 발생시켜
            # DB 예외의 __context__가 연결되지 않도록 합니다.
            raise persistence_error

        acknowledgement_error: ConsumerAcknowledgementError | None = None

        try:
            await self._acknowledger.acknowledge(delivery.stream_message_id)
        except Exception:
            acknowledgement_error = ConsumerAcknowledgementError()

        if acknowledgement_error is not None:
            # commit은 이미 완료됐으므로 rollback하지 않습니다.
            # 활성 예외 구간 밖에서 안전한 오류를 발생시킵니다.
            raise acknowledgement_error

        return result

    async def _rollback_safely(self) -> None:
        """rollback 실패가 원래 저장 실패를 덮어쓰지 않도록 합니다."""

        try:
            await self._transaction.rollback()
        except Exception:
            # 이 계층에서는 원본 DB 예외를 로그나 외부 오류에 포함하지 않습니다.
            return


type LeaseAwareExecutionResult = HandlerSuccess | CommittedDelivery | LeaseNotAcquired


class LeaseAwareConsumerExecution:
    """lease 획득과 fencing 검증을 포함한 Consumer 실행 경계입니다."""

    def __init__(
        self,
        *,
        dispatcher: Dispatcher,
        result_store: ResultStore,
        transaction: Transaction,
        acknowledger: StreamAcknowledger,
        job_repository: JobExecutionRepository,
        heartbeat: LeaseHeartbeat,
        lease_duration: timedelta,
        clock: Callable[[], datetime],
        hard_timeout_seconds: float | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        execution_starter: DomainExecutionStarter | None = None,
    ) -> None:
        if hard_timeout_seconds is not None and (
            isinstance(hard_timeout_seconds, bool)
            or not isinstance(hard_timeout_seconds, int | float)
            or not math.isfinite(hard_timeout_seconds)
            or hard_timeout_seconds <= 0
        ):
            raise ValueError("hard_timeout_seconds는 유한한 양수여야 합니다.")

        self._dispatcher = dispatcher
        self._result_store = result_store
        self._transaction = transaction
        self._acknowledger = acknowledger
        self._job_repository = job_repository
        self._heartbeat = heartbeat
        self._lease_duration = lease_duration
        self._clock = clock
        self._hard_timeout_seconds = None if hard_timeout_seconds is None else float(hard_timeout_seconds)
        self._monotonic_clock = monotonic_clock
        self._execution_starter = execution_starter

    async def execute(
        self,
        delivery: WorkerDelivery,
    ) -> LeaseAwareExecutionResult:
        """lease 소유권이 유지된 결과만 commit한 뒤 ACK합니다."""

        acquired = await self._acquire_lease(delivery)

        if isinstance(acquired, LeaseNotAcquired):
            await self._rollback_safely()
            return acquired

        if isinstance(acquired, CommittedDelivery):
            await self._commit()
            await self._acknowledge(delivery.stream_message_id)
            return acquired
        # AI Job lease·attempt와 도메인 PROCESSING 전이를 같은 짧은
        # transaction으로 확정합니다. Provider 실행 중에는 row lock을
        # 유지하지 않습니다.
        domain_started = await self._start_domain_execution(
            delivery.message,
        )

        if not domain_started:
            await self._rollback_safely()
            return LeaseNotAcquired()

        await self._commit()
        heartbeat_handle = await self._start_heartbeat(acquired)

        result = await self._run_handler(
            delivery,
            heartbeat_handle,
        )

        if isinstance(result, LeaseNotAcquired):
            await self._rollback_safely()
            return result

        ownership_retained = await self._stop_heartbeat(heartbeat_handle)

        if not ownership_retained:
            await self._rollback_safely()
            return LeaseNotAcquired()

        persistence_error: ConsumerPersistenceError | None = None

        try:
            await self._result_store.save(
                message=delivery.message,
                result=result,
            )

            completed = await self._job_repository.complete_execution(
                acquired,
                completed_at=self._clock(),
            )

            if not completed:
                await self._rollback_safely()
                return LeaseNotAcquired()

            await self._transaction.commit()
        except asyncio.CancelledError:
            await self._rollback_safely()
            raise
        except Exception:
            await self._rollback_safely()
            persistence_error = ConsumerPersistenceError()

        if persistence_error is not None:
            raise persistence_error

        await self._acknowledge(delivery.stream_message_id)

        return result

    async def _run_handler(
        self,
        delivery: WorkerDelivery,
        heartbeat_handle: LeaseHeartbeatHandle,
    ) -> HandlerSuccess | LeaseNotAcquired:
        """Handler 실행과 timeout·heartbeat 정리를 담당합니다."""

        execution_context = self._create_execution_context()
        hard_timeout_reached = False

        try:
            return await self._dispatch_with_hard_timeout(
                delivery,
                heartbeat_handle,
                context=execution_context,
            )
        except TimeoutError:
            # timeout으로 Handler task가 취소된 뒤 heartbeat와 현재
            # transaction을 정리합니다. 결과 commit과 ACK는 수행하지 않습니다.
            await self._stop_heartbeat_safely(heartbeat_handle)
            await self._rollback_safely()
            hard_timeout_reached = True
        except BaseException:
            # Dispatcher와 heartbeat 어느 쪽에서 예외가 발생하더라도
            # background heartbeat를 남기지 않습니다.
            await self._stop_heartbeat_safely(heartbeat_handle)
            await self._rollback_safely()
            raise

        if hard_timeout_reached:
            # 활성 TimeoutError 처리 구간 밖에서 승인된 오류를 생성합니다.
            raise WorkerError(failure_code="TIMEOUT") from None

        raise WorkerError(failure_code="INTERNAL_ERROR")

    def _create_execution_context(
        self,
    ) -> HandlerExecutionContext | None:
        """설정된 hard timeout으로 Handler의 절대 deadline을 생성합니다."""

        if self._hard_timeout_seconds is None:
            return None

        return HandlerExecutionContext(
            worker_deadline=(self._monotonic_clock() + self._hard_timeout_seconds),
        )

    async def _dispatch_with_hard_timeout(
        self,
        delivery: WorkerDelivery,
        heartbeat_handle: LeaseHeartbeatHandle,
        *,
        context: HandlerExecutionContext | None,
    ) -> HandlerSuccess | LeaseNotAcquired:
        """동일한 absolute deadline으로 Handler 실행을 제한합니다."""

        if context is None:
            return await self._dispatch_until_heartbeat_ends(
                delivery,
                heartbeat_handle,
                context=None,
            )

        remaining_seconds = context.worker_deadline - self._monotonic_clock()

        if remaining_seconds <= 0:
            raise TimeoutError

        async with asyncio.timeout(remaining_seconds):
            return await self._dispatch_until_heartbeat_ends(
                delivery,
                heartbeat_handle,
                context=context,
            )

    async def _dispatch_until_heartbeat_ends(
        self,
        delivery: WorkerDelivery,
        heartbeat_handle: LeaseHeartbeatHandle,
        *,
        context: HandlerExecutionContext | None,
    ) -> HandlerSuccess | LeaseNotAcquired:
        """Handler 완료와 heartbeat 소유권 상실 중 먼저 발생한 쪽을 처리합니다."""

        dispatch_task = asyncio.create_task(
            self._dispatcher.dispatch(
                delivery.message,
                context=context,
            )
        )
        heartbeat_wait_task = asyncio.create_task(heartbeat_handle.wait())

        try:
            completed_tasks, _ = await asyncio.wait(
                {
                    dispatch_task,
                    heartbeat_wait_task,
                },
                return_when=asyncio.FIRST_COMPLETED,
            )

            if dispatch_task in completed_tasks:
                await self._cancel_task_safely(heartbeat_wait_task)
                return await dispatch_task

            # heartbeat가 먼저 끝났다면 더 이상 소유하지 않는 실행의
            # Provider 호출을 지속하지 않습니다.
            await self._cancel_task_safely(dispatch_task)
            ownership_retained = await self._stop_heartbeat(heartbeat_handle)

            if not ownership_retained:
                return LeaseNotAcquired()

            # Consumer가 stop을 요청하지 않았는데 heartbeat가 먼저
            # 종료된 경우도 실행을 계속하지 않는 보수적 경계로 처리합니다.
            return LeaseNotAcquired()
        except BaseException:
            await self._cancel_task_safely(dispatch_task)
            await self._cancel_task_safely(heartbeat_wait_task)
            raise

    async def _cancel_task_safely(
        self,
        task: asyncio.Task[object],
    ) -> None:
        """취소 대상 task의 예외가 원래 실행 결과를 덮어쓰지 않게 합니다."""

        if not task.done():
            task.cancel()

        try:
            await task
        except BaseException:
            return

    async def _start_domain_execution(
        self,
        message: WorkerMessage,
    ) -> bool:
        """도메인 실행 시작 상태를 현재 lease transaction에 적재합니다."""

        if self._execution_starter is None:
            return True

        persistence_error: ConsumerPersistenceError | None = None

        try:
            started = await self._execution_starter.start(
                message=message,
                started_at=self._clock(),
            )
        except asyncio.CancelledError:
            await self._rollback_safely()
            raise
        except Exception:
            await self._rollback_safely()
            persistence_error = ConsumerPersistenceError()

        if persistence_error is not None:
            raise persistence_error

        return started

    async def _start_heartbeat(
        self,
        lease: ExecutionLease,
    ) -> LeaseHeartbeatHandle:
        persistence_error: ConsumerPersistenceError | None = None

        try:
            heartbeat_handle = await self._heartbeat.start(lease)
        except asyncio.CancelledError:
            await self._rollback_safely()
            raise
        except Exception:
            await self._rollback_safely()
            persistence_error = ConsumerPersistenceError()

        if persistence_error is not None:
            raise persistence_error

        return heartbeat_handle

    async def _stop_heartbeat(
        self,
        heartbeat_handle: LeaseHeartbeatHandle,
    ) -> bool:
        persistence_error: ConsumerPersistenceError | None = None

        try:
            ownership_retained = await heartbeat_handle.stop()
        except asyncio.CancelledError:
            await self._rollback_safely()
            raise
        except Exception:
            await self._rollback_safely()
            persistence_error = ConsumerPersistenceError()

        if persistence_error is not None:
            raise persistence_error

        return ownership_retained

    async def _stop_heartbeat_safely(
        self,
        heartbeat_handle: LeaseHeartbeatHandle,
    ) -> None:
        try:
            await heartbeat_handle.stop()
        except Exception:
            # heartbeat 종료 실패가 원래 Handler 오류를 덮어쓰지 않습니다.
            return

    async def _acquire_lease(
        self,
        delivery: WorkerDelivery,
    ) -> ExecutionLease | CommittedDelivery | LeaseNotAcquired:
        persistence_error: ConsumerPersistenceError | None = None

        try:
            acquired = await self._job_repository.acquire_lease(
                delivery.message,
                now=self._clock(),
                lease_duration=self._lease_duration,
            )
        except asyncio.CancelledError:
            await self._rollback_safely()
            raise
        except Exception:
            await self._rollback_safely()
            persistence_error = ConsumerPersistenceError()

        if persistence_error is not None:
            raise persistence_error

        return acquired

    async def _commit(self) -> None:
        persistence_error: ConsumerPersistenceError | None = None

        try:
            await self._transaction.commit()
        except asyncio.CancelledError:
            await self._rollback_safely()
            raise
        except Exception:
            await self._rollback_safely()
            persistence_error = ConsumerPersistenceError()

        if persistence_error is not None:
            raise persistence_error

    async def _acknowledge(
        self,
        stream_message_id: str,
    ) -> None:
        acknowledgement_error: ConsumerAcknowledgementError | None = None

        try:
            await self._acknowledger.acknowledge(stream_message_id)
        except Exception:
            acknowledgement_error = ConsumerAcknowledgementError()

        if acknowledgement_error is not None:
            raise acknowledgement_error

    async def _rollback_safely(self) -> None:
        """rollback 실패가 원래 실행 오류를 덮어쓰지 않도록 합니다."""

        try:
            await self._transaction.rollback()
        except Exception:
            return
