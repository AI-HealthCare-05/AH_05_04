"""Redis Stream 읽기와 Worker 실행을 반복하는 Consumer runtime입니다."""

import asyncio
from collections.abc import Sequence
from typing import Protocol

from ai_worker.core.stream import WorkerDelivery


class RuntimeStreamConsumer(Protocol):
    """Consumer runtime에 필요한 최소 Stream 계약입니다."""

    async def ensure_consumer_group(self) -> None:
        """Consumer Group이 없으면 생성합니다."""
        ...

    async def read(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5000,
    ) -> Sequence[WorkerDelivery]:
        """처리할 새 메시지를 읽습니다."""
        ...


class DeliveryExecution(Protocol):
    """하나의 Stream delivery를 처리하는 실행 계약입니다."""

    async def execute(self, delivery: WorkerDelivery) -> object:
        """메시지를 처리하고 필요한 commit과 ACK를 완료합니다."""
        ...


class ConsumerRuntime:
    """Stream 읽기와 delivery 실행 순서를 조정합니다."""

    def __init__(
        self,
        *,
        stream: RuntimeStreamConsumer,
        execution: DeliveryExecution,
        consumer_name: str,
        batch_size: int = 1,
        block_ms: int = 5000,
    ) -> None:
        normalized_consumer_name = consumer_name.strip()

        if not normalized_consumer_name:
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if batch_size < 1:
            raise ValueError("batch_size는 1 이상이어야 합니다.")
        if block_ms < 0:
            raise ValueError("block_ms는 0 이상이어야 합니다.")

        self._stream = stream
        self._execution = execution
        self._consumer_name = normalized_consumer_name
        self._batch_size = batch_size
        self._block_ms = block_ms

    async def initialize(self) -> None:
        """메시지를 읽기 전에 Consumer Group을 준비합니다."""

        await self._stream.ensure_consumer_group()

    async def run(self, stop_event: asyncio.Event) -> None:
        """종료 요청 전까지 새 메시지를 읽어 처리합니다."""

        await self.initialize()

        while not stop_event.is_set():
            await self.run_once()

    async def run_once(self) -> int:
        """한 번 읽은 delivery를 설정된 batch 안에서 동시에 처리합니다."""

        deliveries = await self._stream.read(
            consumer_name=self._consumer_name,
            count=self._batch_size,
            block_ms=self._block_ms,
        )

        await asyncio.gather(*(self._execution.execute(delivery) for delivery in deliveries))

        return len(deliveries)
