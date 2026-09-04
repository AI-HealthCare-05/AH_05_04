"""Redis Streams 구현과 Worker 실행 계층 사이의 계약입니다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ai_worker.schemas.messages import WorkerMessage

if TYPE_CHECKING:
    from ai_worker.core.quarantine import RejectedWorkerDelivery


@dataclass(frozen=True, slots=True)
class WorkerDelivery:
    """Redis Stream entry와 검증된 Worker 메시지를 묶습니다."""

    stream_message_id: str
    message: WorkerMessage

    def __post_init__(self) -> None:
        if not self.stream_message_id.strip():
            raise ValueError("stream_message_id는 비어 있을 수 없습니다.")


@dataclass(frozen=True, slots=True)
class PendingMessage:
    """Pending Entry List 조회 결과입니다."""

    stream_message_id: str
    consumer_name: str
    idle_ms: int
    delivery_count: int

    def __post_init__(self) -> None:
        if not self.stream_message_id.strip():
            raise ValueError("stream_message_id는 비어 있을 수 없습니다.")
        if not self.consumer_name.strip():
            raise ValueError("consumer_name은 비어 있을 수 없습니다.")
        if self.idle_ms < 0:
            raise ValueError("idle_ms는 0 이상이어야 합니다.")
        if self.delivery_count < 1:
            raise ValueError("delivery_count는 1 이상이어야 합니다.")


@dataclass(frozen=True, slots=True)
class AutoClaimResult:
    """XAUTOCLAIM의 다음 cursor와 회수 결과입니다."""

    next_start_id: str
    deliveries: tuple[WorkerDelivery | RejectedWorkerDelivery, ...]
    deleted_message_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.next_start_id.strip():
            raise ValueError("next_start_id는 비어 있을 수 없습니다.")


class StreamAcknowledger(Protocol):
    async def acknowledge(self, stream_message_id: str) -> None:
        """처리가 완료된 Stream entry를 ACK합니다."""
        ...


class StreamPublisher(Protocol):
    async def publish(self, message: WorkerMessage) -> str:
        """메시지를 발행하고 Redis Stream entry ID를 반환합니다."""
        ...


class StreamConsumer(StreamAcknowledger, Protocol):
    async def ensure_consumer_group(self) -> None:
        """Consumer Group이 없으면 생성합니다."""
        ...

    async def read(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5000,
    ) -> Sequence[WorkerDelivery | RejectedWorkerDelivery]:
        """Consumer Group을 통해 새 메시지를 읽습니다."""
        ...

    async def list_pending(
        self,
        *,
        count: int = 100,
    ) -> Sequence[PendingMessage]:
        """처리되지 않은 Pending entry를 조회합니다."""
        ...

    async def claim(
        self,
        *,
        consumer_name: str,
        stream_message_ids: Sequence[str],
        min_idle_ms: int,
    ) -> Sequence[WorkerDelivery | RejectedWorkerDelivery]:
        """유휴 시간이 지난 Pending entry의 소유권을 가져옵니다."""
        ...

    async def auto_claim(
        self,
        *,
        consumer_name: str,
        min_idle_ms: int,
        start_id: str = "0-0",
        count: int = 100,
    ) -> AutoClaimResult:
        """유휴 시간이 지난 Pending entry를 cursor 기반으로 회수합니다."""
        ...


class StreamAdapter(StreamPublisher, StreamConsumer, Protocol):
    """Publisher와 Consumer가 공유하는 Redis Streams Adapter 계약입니다."""

    pass
