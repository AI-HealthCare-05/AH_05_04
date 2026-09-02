"""검증된 Worker 메시지를 Stream에 발행하는 서비스입니다."""

from dataclasses import dataclass
from uuid import UUID

from ai_worker.core.stream import StreamPublisher
from ai_worker.schemas.messages import WorkerMessage


@dataclass(frozen=True, slots=True)
class PublishedEventReceipt:
    """Stream 발행 결과를 식별하는 내부 증빙입니다."""

    stream_message_id: str
    event_id: UUID
    job_id: UUID

    def __post_init__(self) -> None:
        if not self.stream_message_id.strip():
            raise ValueError("stream_message_id는 비어 있을 수 없습니다.")


class EventPublisher:
    """Outbox에서 확정된 식별자를 변경하지 않고 발행합니다."""

    def __init__(self, stream: StreamPublisher) -> None:
        self._stream = stream

    async def publish(
        self,
        message: WorkerMessage,
    ) -> PublishedEventReceipt:
        stream_message_id = await self._stream.publish(message)

        return PublishedEventReceipt(
            stream_message_id=stream_message_id,
            event_id=message.event_id,
            job_id=message.job_id,
        )
