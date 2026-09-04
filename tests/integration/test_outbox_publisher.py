"""실제 PostgreSQL·Redis에서 Outbox Publisher 경계를 검증합니다."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai_worker.adapters.redis_stream import RedisStreamAdapter
from ai_worker.adapters.sqlalchemy_outbox_repository import SqlAlchemyOutboxRepository
from ai_worker.core.event_publisher import EventPublisher
from ai_worker.core.outbox_publisher import OutboxPublisher, OutboxPublishStatus

app_core = import_module("app.core")
config = app_core.config

TEST_SCHEMA = "outbox_publisher_test"
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
    connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
)
session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def publisher_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
        await connection.execute(
            text(
                f"""
                CREATE TABLE {TEST_SCHEMA}.ai_job (
                    id VARCHAR(36) PRIMARY KEY,
                    job_type VARCHAR(20) NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                f"""
                CREATE TABLE {TEST_SCHEMA}.outbox_event (
                    event_id VARCHAR(36) PRIMARY KEY,
                    job_id VARCHAR(36) NOT NULL REFERENCES {TEST_SCHEMA}.ai_job(id),
                    attempt INTEGER NOT NULL,
                    event_kind VARCHAR(30) NOT NULL,
                    schema_version VARCHAR(20) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    available_at TIMESTAMPTZ NOT NULL,
                    claim_token VARCHAR(100),
                    claim_expires_at TIMESTAMPTZ,
                    published_at TIMESTAMPTZ,
                    stream_message_id VARCHAR(100),
                    trace_id VARCHAR(100),
                    domain_type VARCHAR(20),
                    domain_id VARCHAR(36)
                )
                """
            )
        )

    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_rows() -> AsyncIterator[None]:
    async with session_factory.begin() as session:
        await session.execute(text("DELETE FROM outbox_event"))
        await session.execute(text("DELETE FROM ai_job"))
    yield


async def insert_event(
    *,
    status: str = "PENDING",
    available_at: datetime,
    claim_token: str | None = None,
    claim_expires_at: datetime | None = None,
    trace_id: str | None = None,
) -> tuple[UUID, UUID, UUID]:
    job_id = uuid4()
    event_id = uuid4()
    domain_id = uuid4()
    async with session_factory.begin() as session:
        await session.execute(
            text("INSERT INTO ai_job (id, job_type) VALUES (:id, 'OCR')"),
            {"id": str(job_id)},
        )
        await session.execute(
            text(
                """
                INSERT INTO outbox_event (
                    event_id, job_id, attempt, event_kind, schema_version,
                    status, available_at, claim_token, claim_expires_at,
                    trace_id, domain_type, domain_id
                ) VALUES (
                    :event_id, :job_id, 1, 'JOB_EXECUTE', '1.0',
                    :status, :available_at, :claim_token, :claim_expires_at,
                    :trace_id, 'OCR_JOB', :domain_id
                )
                """
            ),
            {
                "event_id": str(event_id),
                "job_id": str(job_id),
                "status": status,
                "available_at": available_at,
                "claim_token": claim_token,
                "claim_expires_at": claim_expires_at,
                "trace_id": trace_id or uuid4().hex,
                "domain_id": str(domain_id),
            },
        )
    return event_id, job_id, domain_id


@pytest.mark.asyncio
async def test_two_publishers_cannot_claim_the_same_outbox() -> None:
    now = datetime.now(UTC)
    event_id, _, _ = await insert_event(available_at=now)
    first = SqlAlchemyOutboxRepository(session_factory)
    second = SqlAlchemyOutboxRepository(session_factory)

    results = await asyncio.gather(
        first.claim_available(
            now=now,
            claim_token="first",
            claim_expires_at=now + timedelta(seconds=30),
            limit=1,
        ),
        second.claim_available(
            now=now,
            claim_token="second",
            claim_expires_at=now + timedelta(seconds=30),
            limit=1,
        ),
    )

    claimed = [event for batch in results for event in batch]
    assert [event.event_id for event in claimed] == [event_id]


@pytest.mark.asyncio
async def test_expired_claim_is_reclaimed_but_future_and_active_claims_are_skipped() -> None:
    now = datetime.now(UTC)
    original_trace_id = uuid4().hex
    expired_id, _, _ = await insert_event(
        status="CLAIMED",
        available_at=now - timedelta(minutes=1),
        claim_token="expired",
        claim_expires_at=now - timedelta(seconds=1),
        trace_id=original_trace_id,
    )
    await insert_event(available_at=now + timedelta(minutes=1))
    await insert_event(
        status="CLAIMED",
        available_at=now - timedelta(minutes=1),
        claim_token="active",
        claim_expires_at=now + timedelta(seconds=1),
    )
    repository = SqlAlchemyOutboxRepository(session_factory)

    claimed = await repository.claim_available(
        now=now,
        claim_token="new-owner",
        claim_expires_at=now + timedelta(seconds=30),
        limit=10,
    )

    assert [event.event_id for event in claimed] == [expired_id]
    assert claimed[0].claim_token == "new-owner"
    assert claimed[0].trace_id == original_trace_id


@pytest.mark.asyncio
async def test_only_current_claim_token_can_mark_published() -> None:
    now = datetime.now(UTC)
    event_id, _, _ = await insert_event(available_at=now)
    repository = SqlAlchemyOutboxRepository(session_factory)
    claimed = await repository.claim_available(
        now=now,
        claim_token="current-owner",
        claim_expires_at=now + timedelta(seconds=30),
        limit=1,
    )
    assert len(claimed) == 1

    stale_completed = await repository.mark_published(
        event_id=event_id,
        claim_token="stale-owner",
        stream_message_id="1-0",
        published_at=now,
    )
    current_completed = await repository.mark_published(
        event_id=event_id,
        claim_token="current-owner",
        stream_message_id="2-0",
        published_at=now,
    )

    assert stale_completed is False
    assert current_completed is True
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        """
                    SELECT status, published_at, stream_message_id,
                           claim_token, claim_expires_at
                    FROM outbox_event WHERE event_id = :event_id
                    """
                    ),
                    {"event_id": str(event_id)},
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "PUBLISHED"
    assert row["published_at"] == now
    assert row["stream_message_id"] == "2-0"
    assert row["claim_token"] is None
    assert row["claim_expires_at"] is None


@pytest.mark.asyncio
async def test_postgres_outbox_is_published_to_real_redis() -> None:
    now = datetime.now(UTC)
    event_id, job_id, domain_id = await insert_event(available_at=now)
    stream_name = f"oryak:test:outbox:{uuid4().hex}"
    redis_client = Redis(
        host=os.getenv("TEST_REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("TEST_REDIS_PORT", "6379")),
        password=os.getenv("TEST_REDIS_PASSWORD") or None,
        decode_responses=False,
    )
    stream = RedisStreamAdapter(
        redis_client,
        stream_name=stream_name,
        group_name=f"test-workers-{uuid4().hex}",
    )
    publisher = OutboxPublisher(
        repository=SqlAlchemyOutboxRepository(session_factory),
        event_publisher=EventPublisher(stream),
        clock=lambda: now,
        claim_token_factory=lambda: "publisher-owner",
    )

    try:
        await redis_client.ping()
        results = await publisher.publish_batch()

        assert results[0].status is OutboxPublishStatus.PUBLISHED
        await stream.ensure_consumer_group()
        deliveries = await stream.read(consumer_name="worker", block_ms=100)
        assert len(deliveries) == 1
        assert deliveries[0].message.event_id == event_id
        assert deliveries[0].message.job_id == job_id
        assert deliveries[0].message.domain_id == domain_id
        assert deliveries[0].stream_message_id == results[0].stream_message_id
    finally:
        await redis_client.delete(stream_name)
        await redis_client.aclose()
