"""실제 PostgreSQL에서 DLQ Outbox 선점과 fencing을 검증합니다."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from ai_worker.adapters.redis_stream import RedisStreamAdapter
from ai_worker.adapters.sqlalchemy_dlq_outbox_repository import (
    DlqOutboxStateError,
    SqlAlchemyDlqOutboxRepository,
)
from ai_worker.core.consumer_runtime import ConsumerRuntime
from ai_worker.core.dlq import ClaimedDlqEvent
from ai_worker.core.runtime_assembly import SessionScopedRejectedDeliveryExecution

app_core = import_module("app.core")
config = app_core.config

TEST_SCHEMA = "worker_dlq_outbox_test"

TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database="test",
)

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={
        "server_settings": {
            "search_path": TEST_SCHEMA,
        }
    },
)


async def use_test_schema(session: AsyncSession) -> None:
    await session.execute(text(f"SET search_path TO {TEST_SCHEMA}"))


@pytest_asyncio.fixture(scope="session", autouse=True)
async def repository_schema() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
        await connection.execute(text(f"SET search_path TO {TEST_SCHEMA}"))

        await connection.execute(
            text(
                """
                CREATE TABLE message_quarantine (
                    id VARCHAR(36) PRIMARY KEY,
                    stream_name VARCHAR(100) NOT NULL DEFAULT 'oryak:jobs',
                    stream_entry_id VARCHAR(100) NOT NULL,
                    message_digest VARCHAR(128) NOT NULL,
                    job_id VARCHAR(36),
                    original_event_id VARCHAR(36),
                    failure_code VARCHAR(100) NOT NULL,
                    failure_detail TEXT,
                    original_schema_version VARCHAR(20),
                    trace_id VARCHAR(100),
                    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (stream_name, stream_entry_id)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE dlq_outbox_event (
                    event_id VARCHAR(36) PRIMARY KEY,
                    quarantine_id VARCHAR(36) NOT NULL
                        REFERENCES message_quarantine(id),
                    event_kind VARCHAR(30) NOT NULL DEFAULT 'QUARANTINE_RECORDED',
                    schema_version VARCHAR(20) NOT NULL DEFAULT '1.0',
                    original_schema_version VARCHAR(20),
                    status VARCHAR(20) NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    available_at TIMESTAMPTZ NOT NULL,
                    claim_token VARCHAR(100),
                    claim_expires_at TIMESTAMPTZ,
                    last_error_code VARCHAR(100),
                    published_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (quarantine_id)
                )
                """
            )
        )

    yield

    async with test_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))

    await test_engine.dispose()


async def insert_pending_dlq_event(
    *,
    now: datetime,
) -> tuple[str, str]:
    quarantine_id = str(uuid4())
    event_id = str(uuid4())
    stream_name = f"oryak:jobs:{uuid4().hex}"

    async with test_engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {TEST_SCHEMA}"))
        await connection.execute(
            text(
                """
                INSERT INTO message_quarantine (
                    id,
                    stream_name,
                    stream_entry_id,
                    message_digest,
                    failure_code,
                    trace_id
                )
                VALUES (
                    :id,
                    :stream_name,
                    '1000-0',
                    :message_digest,
                    'INVALID_MESSAGE_SCHEMA',
                    :trace_id
                )
                """
            ),
            {
                "id": quarantine_id,
                "stream_name": stream_name,
                "message_digest": "a" * 64,
                "trace_id": "b" * 32,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO dlq_outbox_event (
                    event_id,
                    quarantine_id,
                    original_schema_version,
                    status,
                    attempt_count,
                    available_at,
                    updated_at
                )
                VALUES (
                    :event_id,
                    :quarantine_id,
                    '2.0',
                    'PENDING',
                    0,
                    :available_at,
                    :updated_at
                )
                """
            ),
            {
                "event_id": event_id,
                "quarantine_id": quarantine_id,
                "available_at": now,
                "updated_at": now,
            },
        )

    return quarantine_id, event_id


@pytest.mark.asyncio
async def test_concurrent_publishers_claim_one_dlq_event_once() -> None:
    now = datetime.now(UTC)
    _, event_id = await insert_pending_dlq_event(now=now)
    barrier = asyncio.Barrier(2)

    async def contend() -> ClaimedDlqEvent | None:
        async with AsyncSession(
            bind=test_engine,
            expire_on_commit=False,
        ) as session:
            await use_test_schema(session)
            await barrier.wait()

            repository = SqlAlchemyDlqOutboxRepository(session)
            claimed = await repository.claim_next(
                now=now,
                claim_expires_at=now + timedelta(seconds=30),
            )
            await session.commit()

            return claimed

    first, second = await asyncio.gather(
        contend(),
        contend(),
    )

    claimed_results = [result for result in (first, second) if result is not None]

    assert len(claimed_results) == 1

    claimed = claimed_results[0]
    assert str(claimed.envelope.event_id) == event_id
    assert claimed.attempt_count == 1

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as session:
        await use_test_schema(session)

        result = await session.execute(
            text(
                """
                SELECT
                    status,
                    attempt_count,
                    claim_token
                FROM dlq_outbox_event
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        )
        status, attempt_count, claim_token = result.one()

    assert status == "CLAIMED"
    assert attempt_count == 1
    assert claim_token == claimed.claim_token


@pytest.mark.asyncio
async def test_stale_claim_token_cannot_publish_dlq_event() -> None:
    now = datetime.now(UTC)
    _, event_id = await insert_pending_dlq_event(now=now)

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as session:
        await use_test_schema(session)

        repository = SqlAlchemyDlqOutboxRepository(session)
        claimed = await repository.claim_next(
            now=now,
            claim_expires_at=now + timedelta(seconds=30),
        )
        await session.commit()

    assert claimed is not None
    assert str(claimed.envelope.event_id) == event_id

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as stale_session:
        await use_test_schema(stale_session)

        stale_repository = SqlAlchemyDlqOutboxRepository(stale_session)

        with pytest.raises(DlqOutboxStateError):
            await stale_repository.mark_published(
                event_id=claimed.envelope.event_id,
                claim_token=uuid4().hex,
                published_at=now,
            )

        await stale_session.rollback()

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as current_session:
        await use_test_schema(current_session)

        current_repository = SqlAlchemyDlqOutboxRepository(current_session)
        await current_repository.mark_published(
            event_id=claimed.envelope.event_id,
            claim_token=claimed.claim_token,
            published_at=now,
        )
        await current_session.commit()

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as reader:
        await use_test_schema(reader)

        result = await reader.execute(
            text(
                """
                SELECT
                    status,
                    claim_token,
                    published_at
                FROM dlq_outbox_event
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        )
        status, claim_token, published_at = result.one()

    assert status == "PUBLISHED"
    assert claim_token is None
    assert published_at == now


@pytest.mark.asyncio
async def test_invalid_stream_message_is_quarantined_before_ack() -> None:
    stream_name = f"oryak:quarantine-test:{uuid4().hex}"
    group_name = f"quarantine-workers-{uuid4().hex}"
    sentinel = "SYNTHETIC_MEDICAL_CONTENT_MUST_NOT_BE_STORED"
    trace_id = uuid4().hex
    redis_client = Redis(
        host=os.getenv("TEST_REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("TEST_REDIS_PORT", "6379")),
        password=os.getenv("TEST_REDIS_PASSWORD") or None,
        decode_responses=False,
    )
    stream = RedisStreamAdapter(
        redis_client,
        stream_name=stream_name,
        group_name=group_name,
    )
    session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        autoflush=False,
    )
    rejected_execution = SessionScopedRejectedDeliveryExecution(
        session_factory=session_factory,
        acknowledger=stream,
        clock=lambda: datetime.now(UTC),
        logger=logging.getLogger("worker-quarantine-integration-test"),
    )
    normal_execution = SimpleNamespace(execute=AsyncMock())
    runtime = ConsumerRuntime(
        stream=stream,
        execution=normal_execution,
        rejected_execution=rejected_execution,
        consumer_name="quarantine-worker-1",
        batch_size=1,
        block_ms=100,
    )

    try:
        await redis_client.ping()
        await runtime.initialize()
        raw_stream_id = await redis_client.xadd(
            stream_name,
            {
                "schema_version": "9.0",
                "trace_id": trace_id,
                "medical_text": sentinel,
            },
        )
        stream_entry_id = raw_stream_id.decode("utf-8") if isinstance(raw_stream_id, bytes) else str(raw_stream_id)

        processed_count = await runtime.run_once()

        async with AsyncSession(
            bind=test_engine,
            expire_on_commit=False,
        ) as observer:
            await use_test_schema(observer)
            quarantine_result = await observer.execute(
                text(
                    """
                    SELECT
                        id,
                        message_digest,
                        failure_code,
                        original_schema_version,
                        trace_id,
                        failure_detail
                    FROM message_quarantine
                    WHERE stream_name = :stream_name
                      AND stream_entry_id = :stream_entry_id
                    """
                ),
                {
                    "stream_name": stream_name,
                    "stream_entry_id": stream_entry_id,
                },
            )
            quarantine_row = quarantine_result.one()

            dlq_result = await observer.execute(
                text(
                    """
                    SELECT
                        event_kind,
                        schema_version,
                        original_schema_version,
                        status,
                        attempt_count
                    FROM dlq_outbox_event
                    WHERE quarantine_id = :quarantine_id
                    """
                ),
                {"quarantine_id": quarantine_row.id},
            )
            dlq_row = dlq_result.one()

        assert processed_count == 1
        assert quarantine_row.failure_code == "UNSUPPORTED_SCHEMA_VERSION"
        assert quarantine_row.original_schema_version == "9.0"
        assert quarantine_row.trace_id == trace_id
        assert len(quarantine_row.message_digest) == 64
        assert quarantine_row.failure_detail is None
        assert sentinel not in repr(quarantine_row)
        assert dlq_row.event_kind == "QUARANTINE_RECORDED"
        assert dlq_row.schema_version == "1.0"
        assert dlq_row.original_schema_version == "9.0"
        assert dlq_row.status == "PENDING"
        assert dlq_row.attempt_count == 0
        assert await stream.list_pending() == ()
        normal_execution.execute.assert_not_awaited()
    finally:
        await redis_client.delete(stream_name)
        await redis_client.aclose()
