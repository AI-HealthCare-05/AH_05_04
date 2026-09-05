"""Redis Streams Adapter 기본 명령 테스트입니다."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    ResponseError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)

from ai_worker.adapters import redis_stream as redis_stream_module
from ai_worker.adapters.errors import (
    StreamOperationError,
)
from ai_worker.adapters.redis_message_codec import encode_stream_message
from ai_worker.adapters.redis_stream import RedisStreamAdapter
from ai_worker.core.quarantine import (
    QuarantineFailureCode,
    RejectedWorkerDelivery,
)
from ai_worker.core.stream import WorkerDelivery
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
            "trace_id": uuid4().hex,
        }
    )


def build_redis_client() -> MagicMock:
    client = MagicMock(spec=Redis)
    client.xautoclaim = AsyncMock()
    client.xgroup_create = AsyncMock()
    client.xadd = AsyncMock()
    client.xreadgroup = AsyncMock()
    client.xack = AsyncMock()
    client.xpending_range = AsyncMock()
    client.xclaim = AsyncMock()

    return client


@pytest.mark.asyncio
async def test_consumer_group_creation_is_idempotent() -> None:
    client = build_redis_client()
    client.xgroup_create.side_effect = ResponseError("BUSYGROUP Consumer Group name already exists")
    adapter = RedisStreamAdapter(cast(Redis, client))
    await adapter.ensure_consumer_group()


@pytest.mark.asyncio
async def test_publish_returns_stream_message_id() -> None:
    client = build_redis_client()
    client.xadd.return_value = b"1000-0"
    adapter = RedisStreamAdapter(cast(Redis, client))
    message = build_message()

    stream_id = await adapter.publish(message)

    assert stream_id == "1000-0"
    fields = client.xadd.await_args.args[1]
    assert fields["event_id"] == str(message.event_id)
    assert "medication_name" not in fields


@pytest.mark.asyncio
async def test_read_decodes_worker_delivery() -> None:
    client = build_redis_client()
    adapter = RedisStreamAdapter(cast(Redis, client))
    message = build_message()
    encoded = encode_stream_message(message)
    fields = {key.encode(): value.encode() for key, value in encoded.items()}
    client.xreadgroup.return_value = [
        (
            b"oryak:jobs",
            [(b"1001-0", fields)],
        )
    ]

    deliveries = await adapter.read(consumer_name="worker-1")

    assert len(deliveries) == 1
    assert isinstance(deliveries[0], WorkerDelivery)
    assert deliveries[0].stream_message_id == "1001-0"
    assert deliveries[0].message == message
    assert deliveries[0].stream_name == "oryak:jobs"
    assert len(deliveries[0].message_digest or "") == 64


@pytest.mark.asyncio
async def test_unknown_ack_is_not_treated_as_success() -> None:
    client = build_redis_client()
    client.xack.return_value = 0
    adapter = RedisStreamAdapter(cast(Redis, client))

    with pytest.raises(StreamOperationError):
        await adapter.acknowledge("unknown-0")


@pytest.mark.asyncio
async def test_pending_entries_are_decoded() -> None:
    client = build_redis_client()
    client.xpending_range.return_value = [
        {
            "message_id": b"1002-0",
            "consumer": b"worker-1",
            "time_since_delivered": 1500,
            "times_delivered": 2,
        }
    ]
    adapter = RedisStreamAdapter(cast(Redis, client))

    pending = await adapter.list_pending()

    assert len(pending) == 1
    assert pending[0].stream_message_id == "1002-0"
    assert pending[0].consumer_name == "worker-1"
    assert pending[0].idle_ms == 1500
    assert pending[0].delivery_count == 2


@pytest.mark.asyncio
async def test_pending_entry_can_be_claimed() -> None:
    client = build_redis_client()
    message = build_message()
    encoded = encode_stream_message(message)
    fields = {key.encode(): value.encode() for key, value in encoded.items()}
    client.xclaim.return_value = [
        (b"1002-0", fields),
    ]
    adapter = RedisStreamAdapter(cast(Redis, client))

    claimed = await adapter.claim(
        consumer_name="worker-2",
        stream_message_ids=["1002-0"],
        min_idle_ms=1000,
    )

    assert len(claimed) == 1
    assert isinstance(claimed[0], WorkerDelivery)
    assert claimed[0].stream_message_id == "1002-0"
    assert claimed[0].message == message
    assert claimed[0].stream_name == "oryak:jobs"
    assert len(claimed[0].message_digest or "") == 64

    client.xclaim.assert_awaited_once_with(
        "oryak:jobs",
        "ai-workers",
        "worker-2",
        1000,
        ["1002-0"],
    )


@pytest.mark.asyncio
async def test_connection_error_is_safely_converted() -> None:
    client = build_redis_client()
    client.xadd.side_effect = RedisConnectionError("SYNTHETIC_REDIS_SECRET")
    adapter = RedisStreamAdapter(cast(Redis, client))

    with pytest.raises(StreamOperationError) as exc_info:
        await adapter.publish(build_message())

    assert "SYNTHETIC_REDIS_SECRET" not in str(exc_info.value)
    assert exc_info.value.__context__ is None


@pytest.mark.asyncio
async def test_timeout_is_safely_converted() -> None:
    client = build_redis_client()
    client.xreadgroup.side_effect = RedisTimeoutError("SYNTHETIC_TIMEOUT_DETAIL")
    adapter = RedisStreamAdapter(cast(Redis, client))

    with pytest.raises(StreamOperationError) as exc_info:
        await adapter.read(consumer_name="worker-1")

    assert "SYNTHETIC_TIMEOUT_DETAIL" not in str(exc_info.value)
    assert exc_info.value.__context__ is None


@pytest.mark.asyncio
async def test_read_isolates_invalid_message_and_returns_valid_delivery() -> None:
    client = build_redis_client()
    adapter = RedisStreamAdapter(cast(Redis, client))
    message = build_message()

    valid_encoded = encode_stream_message(message)
    valid_fields = {key.encode(): value.encode() for key, value in valid_encoded.items()}

    invalid_fields = dict(valid_fields)
    invalid_fields[b"schema_version"] = b"9.0"

    client.xreadgroup.return_value = [
        (
            b"oryak:jobs",
            [
                (b"1003-0", invalid_fields),
                (b"1004-0", valid_fields),
            ],
        )
    ]

    deliveries = await adapter.read(
        consumer_name="worker-1",
        count=2,
    )

    assert len(deliveries) == 2

    rejected = deliveries[0]
    assert isinstance(rejected, RejectedWorkerDelivery)
    assert rejected.stream_entry_id == "1003-0"
    assert rejected.failure_code is QuarantineFailureCode.UNSUPPORTED_SCHEMA_VERSION
    assert rejected.original_schema_version == "9.0"
    assert rejected.job_id == message.job_id
    assert rejected.original_event_id == message.event_id
    assert rejected.trace_id == message.trace_id
    assert len(rejected.message_digest) == 64

    valid = deliveries[1]
    assert isinstance(valid, WorkerDelivery)
    assert valid.stream_message_id == "1004-0"
    assert valid.message == message


@pytest.mark.asyncio
async def test_acknowledge_succeeds_only_for_one_entry() -> None:
    client = build_redis_client()
    client.xack.return_value = 1
    adapter = RedisStreamAdapter(cast(Redis, client))

    await adapter.acknowledge("1004-0")

    client.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_entries_are_auto_claimed() -> None:
    client = build_redis_client()
    message = build_message()
    encoded = encode_stream_message(message)
    fields = {key.encode(): value.encode() for key, value in encoded.items()}

    client.xautoclaim.return_value = [
        b"0-0",
        [(b"1005-0", fields)],
        [b"999-0"],
    ]

    adapter = RedisStreamAdapter(cast(Redis, client))

    result = await adapter.auto_claim(
        consumer_name="worker-2",
        min_idle_ms=1000,
        start_id="0-0",
        count=10,
    )

    assert result.next_start_id == "0-0"
    assert len(result.deliveries) == 1
    assert isinstance(result.deliveries[0], WorkerDelivery)
    assert result.deliveries[0].stream_message_id == "1005-0"
    assert result.deliveries[0].message == message
    assert result.deleted_message_ids == ("999-0",)

    client.xautoclaim.assert_awaited_once_with(
        "oryak:jobs",
        "ai-workers",
        "worker-2",
        1000,
        "0-0",
        count=10,
    )


def test_rejected_delivery_keeps_only_safe_metadata() -> None:
    event_id = uuid4()
    job_id = uuid4()
    trace_id = uuid4().hex
    fields: dict[str | bytes, str | bytes | int | float] = {
        b"schema_version": b"9.0",
        b"event_id": str(event_id).encode(),
        b"job_id": str(job_id).encode(),
        b"trace_id": trace_id.encode(),
        b"raw_prescription": b"SYNTHETIC_SENSITIVE_CONTENT",
    }

    delivery = redis_stream_module._build_rejected_delivery(
        stream_name="oryak:jobs",
        stream_message_id=b"1005-0",
        fields=fields,
    )

    assert isinstance(delivery, RejectedWorkerDelivery)
    assert delivery.stream_entry_id == "1005-0"
    assert delivery.failure_code is QuarantineFailureCode.UNSUPPORTED_SCHEMA_VERSION
    assert delivery.job_id == job_id
    assert delivery.original_event_id == event_id
    assert delivery.original_schema_version == "9.0"
    assert delivery.trace_id == trace_id
    assert len(delivery.message_digest) == 64
    assert "SYNTHETIC_SENSITIVE_CONTENT" not in repr(delivery)


def test_rejected_delivery_digest_is_independent_of_field_order() -> None:
    fields: dict[str | bytes, str | bytes | int | float] = {
        b"schema_version": b"1.0",
        b"event_id": str(uuid4()).encode(),
        b"invalid": b"value",
    }
    reversed_fields: dict[str | bytes, str | bytes | int | float] = dict(reversed(tuple(fields.items())))

    first = redis_stream_module._build_rejected_delivery(
        stream_name="oryak:jobs",
        stream_message_id=b"1006-0",
        fields=fields,
    )
    second = redis_stream_module._build_rejected_delivery(
        stream_name="oryak:jobs",
        stream_message_id=b"1006-0",
        fields=reversed_fields,
    )

    assert first.failure_code is QuarantineFailureCode.INVALID_MESSAGE_SCHEMA
    assert first.message_digest == second.message_digest


@pytest.mark.asyncio
async def test_auto_claim_isolates_invalid_pending_message() -> None:
    client = build_redis_client()
    message = build_message()
    encoded = encode_stream_message(message)
    invalid_fields = {key.encode(): value.encode() for key, value in encoded.items()}
    invalid_fields[b"schema_version"] = b"9.0"

    client.xautoclaim.return_value = [
        b"0-0",
        [(b"1007-0", invalid_fields)],
        [],
    ]
    adapter = RedisStreamAdapter(cast(Redis, client))

    result = await adapter.auto_claim(
        consumer_name="worker-2",
        min_idle_ms=1000,
    )

    assert len(result.deliveries) == 1
    rejected = result.deliveries[0]
    assert isinstance(rejected, RejectedWorkerDelivery)
    assert rejected.stream_entry_id == "1007-0"
    assert rejected.failure_code is QuarantineFailureCode.UNSUPPORTED_SCHEMA_VERSION
    assert rejected.original_schema_version == "9.0"
