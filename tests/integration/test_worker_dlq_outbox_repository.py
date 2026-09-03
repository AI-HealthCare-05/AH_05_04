"""실제 PostgreSQL에서 DLQ Outbox 선점과 fencing을 검증합니다."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from ai_worker.adapters.sqlalchemy_dlq_outbox_repository import (
    DlqOutboxStateError,
    SqlAlchemyDlqOutboxRepository,
)
from ai_worker.core.dlq import ClaimedDlqEvent

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
                    stream_entry_id VARCHAR(100) NOT NULL,
                    message_digest VARCHAR(128) NOT NULL,
                    failure_code VARCHAR(100) NOT NULL,
                    trace_id VARCHAR(100)
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
                    original_schema_version VARCHAR(20),
                    status VARCHAR(20) NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    available_at TIMESTAMPTZ NOT NULL,
                    claim_token VARCHAR(100),
                    claim_expires_at TIMESTAMPTZ,
                    last_error_code VARCHAR(100),
                    published_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL,
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

    async with test_engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {TEST_SCHEMA}"))
        await connection.execute(
            text(
                """
                INSERT INTO message_quarantine (
                    id,
                    stream_entry_id,
                    message_digest,
                    failure_code,
                    trace_id
                )
                VALUES (
                    :id,
                    '1000-0',
                    :message_digest,
                    'INVALID_MESSAGE_SCHEMA',
                    :trace_id
                )
                """
            ),
            {
                "id": quarantine_id,
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
