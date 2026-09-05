"""Redis dead-letter Stream Publisher 테스트입니다."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from ai_worker.adapters.errors import StreamOperationError
from ai_worker.adapters.redis_dead_letter_stream import (
    RedisDeadLetterStreamPublisher,
)
from ai_worker.core.quarantine import (
    DeadLetterEnvelope,
    QuarantineFailureCode,
)


def build_envelope(
    *,
    original_schema_version: str | None = "2.0",
    trace_id: str | None = None,
) -> DeadLetterEnvelope:
    return DeadLetterEnvelope(
        event_id=uuid4(),
        quarantine_id=uuid4(),
        stream_entry_id="1000-0",
        message_digest="a" * 64,
        failure_code=QuarantineFailureCode.UNSUPPORTED_SCHEMA_VERSION,
        original_schema_version=original_schema_version,
        trace_id=trace_id,
    )


@pytest.mark.asyncio
async def test_publish_writes_only_safe_dead_letter_fields() -> None:
    client = MagicMock()
    client.xadd = AsyncMock(return_value=b"9000-0")
    envelope = build_envelope(trace_id="b" * 32)

    publisher = RedisDeadLetterStreamPublisher(client)

    stream_message_id = await publisher.publish(envelope)

    assert stream_message_id == "9000-0"

    client.xadd.assert_awaited_once()
    stream_name, fields = client.xadd.await_args.args

    assert stream_name == "oryak:jobs:dead-letter"
    assert fields == {
        "schema_version": "1.0",
        "event_kind": "QUARANTINE_RECORDED",
        "event_id": str(envelope.event_id),
        "quarantine_id": str(envelope.quarantine_id),
        "stream_entry_id": envelope.stream_entry_id,
        "message_digest": envelope.message_digest,
        "failure_code": envelope.failure_code.value,
        "original_schema_version": "2.0",
        "trace_id": "b" * 32,
    }

    forbidden_fields = {
        "job_id",
        "user_id",
        "message",
        "payload",
        "raw_value",
        "normalized_value",
        "medical_document",
    }
    assert forbidden_fields.isdisjoint(fields)


@pytest.mark.asyncio
async def test_publish_omits_nullable_dead_letter_fields() -> None:
    client = MagicMock()
    client.xadd = AsyncMock(return_value="9001-0")
    envelope = build_envelope(
        original_schema_version=None,
        trace_id=None,
    )

    publisher = RedisDeadLetterStreamPublisher(
        client,
        stream_name="custom:dead-letter",
    )

    stream_message_id = await publisher.publish(envelope)

    assert stream_message_id == "9001-0"

    stream_name, fields = client.xadd.await_args.args

    assert stream_name == "custom:dead-letter"
    assert "original_schema_version" not in fields
    assert "trace_id" not in fields


@pytest.mark.asyncio
async def test_publish_converts_redis_error_to_safe_stream_error() -> None:
    client = MagicMock()
    client.xadd = AsyncMock(side_effect=RedisError("synthetic Redis detail must not escape"))
    envelope = build_envelope()

    publisher = RedisDeadLetterStreamPublisher(client)

    with pytest.raises(StreamOperationError) as exc_info:
        await publisher.publish(envelope)

    assert "synthetic Redis detail must not escape" not in str(exc_info.value)
