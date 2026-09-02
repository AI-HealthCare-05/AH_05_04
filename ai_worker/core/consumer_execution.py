"""Handler 결과 검증 이후 저장·commit·ACK 순서를 조정합니다."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import (
    ConsumerAcknowledgementError,
    ConsumerPersistenceError,
    WorkerError,
)
from ai_worker.core.job_execution import (
    CommittedDelivery,
    ExecutionLease,
    JobExecutionRepository,
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
        lease_duration: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        self._dispatcher = dispatcher
        self._result_store = result_store
        self._transaction = transaction
        self._acknowledger = acknowledger
        self._job_repository = job_repository
        self._lease_duration = lease_duration
        self._clock = clock

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

        try:
            result = await self._dispatcher.dispatch(delivery.message)
        except (WorkerError, asyncio.CancelledError):
            await self._rollback_safely()
            raise

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
