"""Event Publisher 서비스 테스트입니다."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.adapters.fake_stream import FakeStreamAdapter
from ai_worker.core.event_publisher import EventPublisher
from ai_worker.schemas.messages import WorkerMessage


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
            "trace_id": "event-publisher-test",
        }
    )


@pytest.mark.asyncio
async def test_publisher_returns_identifiers_without_changes() -> None:
    adapter = FakeStreamAdapter()
    publisher = EventPublisher(adapter)
    message = build_message()

    receipt = await publisher.publish(message)

    assert receipt.stream_message_id == "1-0"
    assert receipt.event_id == message.event_id
    assert receipt.job_id == message.job_id


@pytest.mark.asyncio
async def test_duplicate_publish_preserves_event_id() -> None:
    adapter = FakeStreamAdapter()
    publisher = EventPublisher(adapter)
    message = build_message()

    first = await publisher.publish(message)
    second = await publisher.publish(message)

    assert first.stream_message_id != second.stream_message_id
    assert first.event_id == second.event_id == message.event_id
    assert first.job_id == second.job_id == message.job_id
