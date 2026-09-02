"""로컬 Redis를 사용하는 Stream Adapter 통합 테스트입니다."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from ai_worker.adapters.redis_stream import RedisStreamAdapter
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
            "trace_id": "redis-integration-test",
        }
    )


@pytest.mark.asyncio
async def test_real_redis_stream_adapter_one_cycle() -> None:
    redis_host = os.getenv("TEST_REDIS_HOST", "127.0.0.1")
    redis_port = int(os.getenv("TEST_REDIS_PORT", "6379"))
    redis_password = os.getenv("TEST_REDIS_PASSWORD") or None

    stream_name = f"oryak:test:{uuid4().hex}"
    group_name = f"test-workers-{uuid4().hex}"

    client = Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        decode_responses=False,
    )
    adapter = RedisStreamAdapter(
        client,
        stream_name=stream_name,
        group_name=group_name,
    )

    try:
        await client.ping()
        await adapter.ensure_consumer_group()

        message = build_message()
        stream_id = await adapter.publish(message)

        deliveries = await adapter.read(
            consumer_name="worker-1",
            block_ms=100,
        )

        assert len(deliveries) == 1
        assert deliveries[0].stream_message_id == stream_id
        assert deliveries[0].message == message

        pending = await adapter.list_pending()

        assert len(pending) == 1
        assert pending[0].consumer_name == "worker-1"

        claimed = await adapter.claim(
            consumer_name="worker-2",
            stream_message_ids=[stream_id],
            min_idle_ms=0,
        )

        assert len(claimed) == 1
        assert claimed[0].stream_message_id == stream_id

        await adapter.acknowledge(stream_id)

        assert await adapter.list_pending() == ()

        # 같은 event가 중복 발행돼도 각각의 Stream entry로 전달됩니다.
        first_duplicate_id = await adapter.publish(message)
        second_duplicate_id = await adapter.publish(message)

        duplicate_deliveries = await adapter.read(
            consumer_name="worker-1",
            count=2,
            block_ms=100,
        )

        assert first_duplicate_id != second_duplicate_id
        assert len(duplicate_deliveries) == 2
        assert {delivery.message.event_id for delivery in duplicate_deliveries} == {message.event_id}

        for delivery in duplicate_deliveries:
            await adapter.acknowledge(delivery.stream_message_id)
    finally:
        # 테스트마다 고유하게 만든 Stream만 정리합니다.
        await client.delete(stream_name)
        await client.aclose()
