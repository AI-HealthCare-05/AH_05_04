"""DB Outbox 선점·WorkerMessage 조립·발행 완료 경계 테스트입니다."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ai_worker.adapters.fake_stream import FakeStreamAdapter
from ai_worker.core.event_publisher import EventPublisher
from ai_worker.core.outbox_publisher import (
    ClaimedOutboxEvent,
    OutboxPublisher,
    OutboxPublishStatus,
)
from ai_worker.schemas.messages import WorkerMessage


class FakeOutboxRepository:
    def __init__(self, events: Sequence[ClaimedOutboxEvent]) -> None:
        self.events = tuple(events)
        self.claim_calls: list[dict[str, object]] = []
        self.mark_calls: list[dict[str, object]] = []
        self.mark_result = True

    async def claim_available(
        self,
        *,
        now: datetime,
        claim_token: str,
        claim_expires_at: datetime,
        limit: int,
    ) -> Sequence[ClaimedOutboxEvent]:
        self.claim_calls.append(
            {
                "now": now,
                "claim_token": claim_token,
                "claim_expires_at": claim_expires_at,
                "limit": limit,
            }
        )
        return self.events[:limit]

    async def mark_published(
        self,
        *,
        event_id: UUID,
        claim_token: str,
        stream_message_id: str,
        published_at: datetime,
    ) -> bool:
        self.mark_calls.append(
            {
                "event_id": event_id,
                "claim_token": claim_token,
                "stream_message_id": stream_message_id,
                "published_at": published_at,
            }
        )
        return self.mark_result


class FailingStreamAdapter(FakeStreamAdapter):
    async def publish(self, message: WorkerMessage) -> str:
        raise ConnectionError("synthetic redis failure")


def build_claimed_event(
    *,
    trace_id: str | None = None,
    claim_token: str = "claim-token",
) -> ClaimedOutboxEvent:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    return ClaimedOutboxEvent(
        event_id=uuid4(),
        job_id=uuid4(),
        job_type="OCR",
        event_kind="JOB_EXECUTE",
        schema_version="1.0",
        domain_type="OCR_JOB",
        domain_id=uuid4(),
        attempt=1,
        available_at=now,
        trace_id=trace_id if trace_id is not None else uuid4().hex,
        claim_token=claim_token,
    )


@pytest.mark.asyncio
async def test_publish_batch_preserves_envelope_and_marks_claim_owner_published() -> None:
    event = build_claimed_event(claim_token="batch-claim")
    repository = FakeOutboxRepository([event])
    stream = FakeStreamAdapter()
    now = datetime(2026, 9, 4, 1, 2, 3, tzinfo=UTC)
    publisher = OutboxPublisher(
        repository=repository,
        event_publisher=EventPublisher(stream),
        clock=lambda: now,
        claim_token_factory=lambda: "batch-claim",
    )

    results = await publisher.publish_batch()

    assert results[0].status is OutboxPublishStatus.PUBLISHED
    assert results[0].stream_message_id == "1-0"
    assert repository.claim_calls == [
        {
            "now": now,
            "claim_token": "batch-claim",
            "claim_expires_at": now + timedelta(seconds=30),
            "limit": 100,
        }
    ]
    assert repository.mark_calls == [
        {
            "event_id": event.event_id,
            "claim_token": event.claim_token,
            "stream_message_id": "1-0",
            "published_at": now,
        }
    ]

    await stream.ensure_consumer_group()
    deliveries = await stream.read(consumer_name="worker", block_ms=0)
    assert len(deliveries) == 1
    message = deliveries[0].message
    assert message.schema_version == event.schema_version
    assert message.event_id == event.event_id
    assert message.event_kind == event.event_kind
    assert message.job_id == event.job_id
    assert message.job_type == event.job_type
    assert message.domain_type == event.domain_type
    assert message.domain_id == event.domain_id
    assert message.attempt == event.attempt
    assert message.available_at == event.available_at
    assert message.trace_id == event.trace_id
    assert message.enqueued_at == now


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_id", [None, "", "INVALID", "A" * 32])
async def test_invalid_trace_id_is_not_corrected_or_published(trace_id: str | None) -> None:
    event = replace(build_claimed_event(), trace_id=trace_id)
    repository = FakeOutboxRepository([event])
    stream = FakeStreamAdapter()
    publisher = OutboxPublisher(
        repository=repository,
        event_publisher=EventPublisher(stream),
        clock=lambda: datetime.now(UTC),
    )

    results = await publisher.publish_batch()

    assert len(results) == 1
    assert results[0].status is OutboxPublishStatus.INVALID_MESSAGE
    assert results[0].stream_message_id is None
    assert repository.mark_calls == []
    await stream.ensure_consumer_group()
    assert await stream.read(consumer_name="worker", block_ms=0) == ()


@pytest.mark.asyncio
async def test_publish_failure_does_not_mark_outbox_published() -> None:
    event = build_claimed_event()
    repository = FakeOutboxRepository([event])
    publisher = OutboxPublisher(
        repository=repository,
        event_publisher=EventPublisher(FailingStreamAdapter()),
        clock=lambda: datetime.now(UTC),
    )

    results = await publisher.publish_batch()

    assert results[0].status is OutboxPublishStatus.PUBLISH_FAILED
    assert repository.mark_calls == []


@pytest.mark.asyncio
async def test_fencing_failure_reports_ownership_loss_after_stream_publish() -> None:
    event = build_claimed_event()
    repository = FakeOutboxRepository([event])
    repository.mark_result = False
    publisher = OutboxPublisher(
        repository=repository,
        event_publisher=EventPublisher(FakeStreamAdapter()),
        clock=lambda: datetime.now(UTC),
    )

    results = await publisher.publish_batch()

    assert results[0].status is OutboxPublishStatus.OWNERSHIP_LOST
    assert results[0].stream_message_id == "1-0"
    assert len(repository.mark_calls) == 1


@pytest.mark.parametrize(
    ("claim_lease", "batch_size"),
    [
        (timedelta(0), 1),
        (timedelta(seconds=30), 0),
        (timedelta(seconds=30), 101),
    ],
)
def test_invalid_publisher_settings_are_rejected(
    claim_lease: timedelta,
    batch_size: int,
) -> None:
    with pytest.raises(ValueError):
        OutboxPublisher(
            repository=FakeOutboxRepository([]),
            event_publisher=EventPublisher(FakeStreamAdapter()),
            clock=lambda: datetime.now(UTC),
            claim_lease=claim_lease,
            batch_size=batch_size,
        )
