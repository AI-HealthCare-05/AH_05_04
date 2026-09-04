"""DB Outbox 선점부터 Stream 발행 완료까지 조정합니다."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from ai_worker.core.event_publisher import EventPublisher
from ai_worker.schemas.messages import WorkerMessage

MAX_OUTBOX_BATCH_SIZE = 100

@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    """발행 lease를 획득한 Outbox row의 비민감 envelope 필드입니다."""

    event_id: UUID
    job_id: UUID
    job_type: str
    event_kind: str
    schema_version: str
    domain_type: str | None
    domain_id: UUID | None
    attempt: int
    available_at: datetime
    trace_id: str | None
    claim_token: str


class OutboxPublishStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    OWNERSHIP_LOST = "OWNERSHIP_LOST"


@dataclass(frozen=True, slots=True)
class OutboxPublishResult:
    """한 Outbox event의 발행 결과입니다. 원본 오류는 포함하지 않습니다."""

    event_id: UUID
    status: OutboxPublishStatus
    stream_message_id: str | None = None


class OutboxRepository(Protocol):
    async def claim_available(
        self,
        *,
        now: datetime,
        claim_token: str,
        claim_expires_at: datetime,
        limit: int,
    ) -> Sequence[ClaimedOutboxEvent]:
        """발행 가능한 row를 원자적으로 선점하고 commit합니다."""
        ...

    async def mark_published(
        self,
        *,
        event_id: UUID,
        claim_token: str,
        stream_message_id: str,
        published_at: datetime,
    ) -> bool:
        """현재 claim 소유자만 발행 완료를 원자적으로 기록합니다."""
        ...


class OutboxPublisher:
    """짧은 DB claim과 외부 Stream I/O를 분리해 Outbox를 전달합니다."""

    def __init__(
        self,
        *,
        repository: OutboxRepository,
        event_publisher: EventPublisher,
        clock: Callable[[], datetime],
        claim_lease: timedelta = timedelta(seconds=30),
        batch_size: int = MAX_OUTBOX_BATCH_SIZE,
        claim_token_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        if claim_lease <= timedelta(0):
            raise ValueError("claim_lease는 양수여야 합니다.")
        if not 1 <= batch_size <= MAX_OUTBOX_BATCH_SIZE:
            raise ValueError(
                f"batch_size는 1 이상 {MAX_OUTBOX_BATCH_SIZE} 이하여야 합니다."
            )

        self._repository = repository
        self._event_publisher = event_publisher
        self._clock = clock
        self._claim_lease = claim_lease
        self._batch_size = batch_size
        self._claim_token_factory = claim_token_factory

    async def publish_batch(self) -> tuple[OutboxPublishResult, ...]:
        """due Outbox를 선점하고 각각 발행한 뒤 fencing 조건으로 완료합니다."""

        claimed_at = self._clock()
        claim_token = self._claim_token_factory()

        if not claim_token.strip():
            raise ValueError("claim_token은 비어 있을 수 없습니다.")

        events = await self._repository.claim_available(
            now=claimed_at,
            claim_token=claim_token,
            claim_expires_at=claimed_at + self._claim_lease,
            limit=self._batch_size,
        )

        results: list[OutboxPublishResult] = []

        for event in events:
            try:
                message = self._build_message(event)
            except ValueError:
                results.append(
                    OutboxPublishResult(
                        event_id=event.event_id,
                        status=OutboxPublishStatus.INVALID_MESSAGE,
                    )
                )
                continue

            try:
                receipt = await self._event_publisher.publish(message)
            except Exception:
                results.append(
                    OutboxPublishResult(
                        event_id=event.event_id,
                        status=OutboxPublishStatus.PUBLISH_FAILED,
                    )
                )
                continue

            completed = await self._repository.mark_published(
                event_id=event.event_id,
                claim_token=event.claim_token,
                stream_message_id=receipt.stream_message_id,
                published_at=self._clock(),
            )

            results.append(
                OutboxPublishResult(
                    event_id=event.event_id,
                    status=(OutboxPublishStatus.PUBLISHED if completed else OutboxPublishStatus.OWNERSHIP_LOST),
                    stream_message_id=receipt.stream_message_id,
                )
            )

        return tuple(results)

    def _build_message(self, event: ClaimedOutboxEvent) -> WorkerMessage:
        """DB에 저장된 식별자를 보정하지 않고 WorkerMessage로 검증합니다."""

        return WorkerMessage.model_validate(
            {
                "schema_version": event.schema_version,
                "event_id": event.event_id,
                "event_kind": event.event_kind,
                "job_id": event.job_id,
                "job_type": event.job_type,
                "domain_type": event.domain_type,
                "domain_id": event.domain_id,
                "attempt": event.attempt,
                "available_at": event.available_at,
                "enqueued_at": self._clock(),
                "trace_id": event.trace_id,
            }
        )
