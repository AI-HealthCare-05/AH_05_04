"""Handler 결과 검증 이후 저장·commit·ACK 순서를 조정합니다."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.errors import (
    ConsumerAcknowledgementError,
    ConsumerPersistenceError,
    WorkerError,
)
from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import WorkerMessage


@dataclass(frozen=True, slots=True)
class WorkerDelivery:
    """Redis Stream 전달 식별자와 검증된 Worker 메시지를 묶습니다.

    stream_message_id는 Redis ACK에 사용하고,
    WorkerMessage.event_id는 비즈니스 이벤트 식별자로 사용합니다.
    """

    stream_message_id: str
    message: WorkerMessage

    def __post_init__(self) -> None:
        """빈 Stream 메시지 ID를 실행 경계에서 거부합니다."""

        if not self.stream_message_id.strip():
            raise ValueError("stream_message_id는 비어 있을 수 없습니다.")


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


class StreamAcknowledger(Protocol):
    """DB commit 이후 Stream 메시지를 ACK하는 인터페이스입니다."""

    async def acknowledge(self, stream_message_id: str) -> None:
        """Redis Stream 메시지를 ACK합니다."""
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
