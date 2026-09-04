"""Fake Stream Adapter 계약 테스트입니다."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.adapters.fake_stream import FakeStreamAdapter
from ai_worker.core.stream import StreamAdapter, WorkerDelivery
from ai_worker.schemas.messages import WorkerMessage


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds / 1000


def build_message() -> WorkerMessage:
    now = datetime.now(UTC)

    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(uuid4()),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(uuid4()),
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": uuid4().hex,
        }
    )


@pytest.mark.asyncio
async def test_publish_read_and_ack() -> None:
    adapter: StreamAdapter = FakeStreamAdapter()
    await adapter.ensure_consumer_group()

    stream_id = await adapter.publish(build_message())
    deliveries = await adapter.read(consumer_name="worker-1")

    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert isinstance(delivery, WorkerDelivery)
    assert delivery.stream_message_id == stream_id
    assert len(await adapter.list_pending()) == 1

    await adapter.acknowledge(stream_id)

    assert await adapter.list_pending() == ()


@pytest.mark.asyncio
async def test_duplicate_event_is_delivered_with_distinct_stream_ids() -> None:
    adapter: StreamAdapter = FakeStreamAdapter()
    await adapter.ensure_consumer_group()
    message = build_message()

    first_id = await adapter.publish(message)
    second_id = await adapter.publish(message)
    deliveries = await adapter.read(
        consumer_name="worker-1",
        count=2,
    )

    assert first_id != second_id
    assert len(deliveries) == 2
    first_delivery, second_delivery = deliveries
    assert isinstance(first_delivery, WorkerDelivery)
    assert isinstance(second_delivery, WorkerDelivery)
    assert first_delivery.message.event_id == message.event_id
    assert second_delivery.message.event_id == message.event_id


@pytest.mark.asyncio
async def test_pending_message_can_be_claimed_after_idle_time() -> None:
    clock = FakeClock()
    adapter: StreamAdapter = FakeStreamAdapter(clock=clock)
    await adapter.ensure_consumer_group()

    stream_id = await adapter.publish(build_message())
    await adapter.read(consumer_name="worker-1")

    assert (
        await adapter.claim(
            consumer_name="worker-2",
            stream_message_ids=[stream_id],
            min_idle_ms=1000,
        )
        == ()
    )

    clock.advance(1000)

    claimed = await adapter.claim(
        consumer_name="worker-2",
        stream_message_ids=[stream_id],
        min_idle_ms=1000,
    )
    pending = await adapter.list_pending()

    assert len(claimed) == 1
    claimed_delivery = claimed[0]
    assert isinstance(claimed_delivery, WorkerDelivery)
    assert claimed_delivery.stream_message_id == stream_id
    assert pending[0].consumer_name == "worker-2"
    assert pending[0].delivery_count == 2


@pytest.mark.asyncio
async def test_pending_message_can_be_auto_claimed_after_idle_time() -> None:
    clock = FakeClock()
    adapter: StreamAdapter = FakeStreamAdapter(clock=clock)
    await adapter.ensure_consumer_group()

    stream_id = await adapter.publish(build_message())
    await adapter.read(consumer_name="worker-1")

    before_expiry = await adapter.auto_claim(
        consumer_name="worker-2",
        min_idle_ms=1000,
    )

    assert before_expiry.deliveries == ()

    clock.advance(1000)

    result = await adapter.auto_claim(
        consumer_name="worker-2",
        min_idle_ms=1000,
    )
    pending = await adapter.list_pending()

    assert result.next_start_id == "0-0"
    assert len(result.deliveries) == 1
    claimed_delivery = result.deliveries[0]
    assert isinstance(claimed_delivery, WorkerDelivery)
    assert claimed_delivery.stream_message_id == stream_id
    assert result.deleted_message_ids == ()
    assert pending[0].consumer_name == "worker-2"
    assert pending[0].delivery_count == 2


@pytest.mark.asyncio
async def test_unknown_ack_is_not_treated_as_success() -> None:
    adapter = FakeStreamAdapter()

    with pytest.raises(LookupError):
        await adapter.acknowledge("unknown-0")
