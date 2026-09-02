"""Redis 없이 Stream 계약을 검증하는 메모리 Fake Adapter입니다."""

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ai_worker.core.stream import PendingMessage, WorkerDelivery
from ai_worker.schemas.messages import WorkerMessage


@dataclass(slots=True)
class _PendingState:
    delivery: WorkerDelivery
    consumer_name: str
    delivered_at: float
    delivery_count: int = 1


class FakeStreamAdapter:
    """IT-1과 단위 테스트에서 사용하는 메모리 Stream Adapter입니다."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._sequence = 0
        self._group_created = False
        self._ready: deque[WorkerDelivery] = deque()
        self._pending: dict[str, _PendingState] = {}

    async def ensure_consumer_group(self) -> None:
        self._group_created = True

    async def publish(self, message: WorkerMessage) -> str:
        self._sequence += 1
        stream_message_id = f"{self._sequence}-0"

        self._ready.append(
            WorkerDelivery(
                stream_message_id=stream_message_id,
                message=message,
            )
        )

        return stream_message_id

    async def read(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5000,
    ) -> Sequence[WorkerDelivery]:
        self._require_group()

        if not consumer_name.strip():
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if count < 1:
            raise ValueError("count는 1 이상이어야 합니다.")
        if block_ms < 0:
            raise ValueError("block_ms는 0 이상이어야 합니다.")

        deliveries: list[WorkerDelivery] = []
        delivered_at = self._clock()

        for _ in range(min(count, len(self._ready))):
            delivery = self._ready.popleft()
            self._pending[delivery.stream_message_id] = _PendingState(
                delivery=delivery,
                consumer_name=consumer_name,
                delivered_at=delivered_at,
            )
            deliveries.append(delivery)

        return tuple(deliveries)

    async def acknowledge(self, stream_message_id: str) -> None:
        if self._pending.pop(stream_message_id, None) is None:
            raise LookupError("ACK 대상 Stream entry를 찾을 수 없습니다.")

    async def list_pending(
        self,
        *,
        count: int = 100,
    ) -> Sequence[PendingMessage]:
        if count < 1:
            raise ValueError("count는 1 이상이어야 합니다.")

        now = self._clock()
        pending_messages: list[PendingMessage] = []

        for stream_message_id, state in list(self._pending.items())[:count]:
            pending_messages.append(
                PendingMessage(
                    stream_message_id=stream_message_id,
                    consumer_name=state.consumer_name,
                    idle_ms=max(
                        0,
                        int((now - state.delivered_at) * 1000),
                    ),
                    delivery_count=state.delivery_count,
                )
            )

        return tuple(pending_messages)

    async def claim(
        self,
        *,
        consumer_name: str,
        stream_message_ids: Sequence[str],
        min_idle_ms: int,
    ) -> Sequence[WorkerDelivery]:
        if not consumer_name.strip():
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if min_idle_ms < 0:
            raise ValueError("min_idle_ms는 0 이상이어야 합니다.")

        now = self._clock()
        claimed: list[WorkerDelivery] = []

        for stream_message_id in stream_message_ids:
            state = self._pending.get(stream_message_id)

            if state is None:
                continue

            idle_ms = int((now - state.delivered_at) * 1000)

            if idle_ms < min_idle_ms:
                continue

            state.consumer_name = consumer_name
            state.delivered_at = now
            state.delivery_count += 1
            claimed.append(state.delivery)

        return tuple(claimed)

    def _require_group(self) -> None:
        if not self._group_created:
            raise RuntimeError("Consumer Group이 생성되지 않았습니다.")
