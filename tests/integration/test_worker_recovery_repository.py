"""실제 PostgreSQL·Redis에서 Worker 복구와 retry Outbox 경계를 검증합니다."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai_worker.adapters.redis_stream import RedisStreamAdapter
from ai_worker.adapters.sqlalchemy_job_execution_repository import (
    SqlAlchemyJobExecutionRepository,
)
from ai_worker.adapters.sqlalchemy_outbox_repository import SqlAlchemyOutboxRepository
from ai_worker.adapters.sqlalchemy_recovery_repository import SqlAlchemyRecoveryRepository
from ai_worker.core.event_publisher import EventPublisher
from ai_worker.core.job_execution import ExecutionLease
from ai_worker.core.outbox_publisher import OutboxPublisher, OutboxPublishStatus
from ai_worker.core.recovery import ExpiredExecution, RecoveryDisposition
from ai_worker.core.retry import FailureCode
from ai_worker.core.stream import WorkerDelivery

app_core = import_module("app.core")
config = app_core.config

TEST_SCHEMA = "worker_recovery_repository_test"
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
async def recovery_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
        await connection.execute(
            text(
                f"""
                CREATE TABLE {TEST_SCHEMA}.ai_job (
                    id VARCHAR(36) PRIMARY KEY,
                    job_type VARCHAR(20) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    expected_event_id VARCHAR(36),
                    last_consumed_event_id VARCHAR(36),
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    available_at TIMESTAMPTZ NOT NULL,
                    lease_token VARCHAR(100),
                    lease_expires_at TIMESTAMPTZ,
                    heartbeat_at TIMESTAMPTZ,
                    failure_code VARCHAR(100),
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ
                )
                """
            )
        )
        await connection.execute(
            text(
                f"""
                CREATE TABLE {TEST_SCHEMA}.outbox_event (
                    event_id VARCHAR(36) PRIMARY KEY,
                    job_id VARCHAR(36) NOT NULL,
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
                    domain_id VARCHAR(36),
                    UNIQUE (job_id, attempt, event_kind)
                )
                """
            )
        )
        await connection.execute(
            text(
                f"""
                CREATE TABLE {TEST_SCHEMA}.ai_job_attempt (
                    id VARCHAR(36) PRIMARY KEY,
                    ai_job_id VARCHAR(36) NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    attempt_status VARCHAR(30) NOT NULL,
                    error_code VARCHAR(100),
                    retryable BOOLEAN NOT NULL,
                    timed_out BOOLEAN NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    UNIQUE (ai_job_id, attempt_no)
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
        await session.execute(text("DELETE FROM ai_job_attempt"))
        await session.execute(text("DELETE FROM outbox_event"))
        await session.execute(text("DELETE FROM ai_job"))
    yield


@pytest.mark.asyncio
async def test_expired_execution_updates_real_boolean_attempt_columns() -> None:
    now = datetime.now(UTC)
    job_id = uuid4()
    event_id = uuid4()
    lease_expires_at = now - timedelta(seconds=1)

    async with session_factory.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO ai_job (
                    id, job_type, status, expected_event_id,
                    attempt_count, max_attempts, available_at,
                    lease_token, lease_expires_at, heartbeat_at
                ) VALUES (
                    :job_id, 'OCR', 'PROCESSING', :event_id,
                    1, 3, :now, 'lease-owner', :lease_expires_at, :now
                )
                """
            ),
            {
                "job_id": str(job_id),
                "event_id": str(event_id),
                "now": now,
                "lease_expires_at": lease_expires_at,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO ai_job_attempt (
                    id, ai_job_id, attempt_no, attempt_status,
                    retryable, timed_out, started_at
                ) VALUES (
                    :attempt_id, :job_id, 1, 'PROCESSING', false, false, :now
                )
                """
            ),
            {
                "attempt_id": str(uuid4()),
                "job_id": str(job_id),
                "now": now,
            },
        )

    async with session_factory.begin() as session:
        disposition = await SqlAlchemyRecoveryRepository(session).recover_expired_execution(
            ExpiredExecution(
                job_id=job_id,
                event_id=event_id,
                attempt=1,
                max_attempts=3,
                lease_expires_at=lease_expires_at,
            ),
            now=now,
            retry_at=now + timedelta(seconds=5),
            failure_code="TIMEOUT",
        )

    assert disposition is RecoveryDisposition.RETRY_WAIT
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT j.status, a.error_code, a.retryable, a.timed_out
                    FROM ai_job AS j
                    JOIN ai_job_attempt AS a ON a.ai_job_id = j.id
                    WHERE j.id = :job_id
                    """
                ),
                {"job_id": str(job_id)},
            )
        ).one()

    assert row.status == "RETRY_WAIT"
    assert row.error_code == "TIMEOUT"
    assert row.retryable is True
    assert row.timed_out is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_code", "expects_retry"),
    [
        ("TIMEOUT", True),
        ("DEPENDENCY_UNAVAILABLE", True),
        ("INVALID_INPUT", False),
        ("UNSUPPORTED_SCHEMA", False),
        ("SAFETY_VALIDATION_FAILED", False),
        ("RETRY_EXHAUSTED", False),
        ("INTERNAL_ERROR", False),
    ],
)
async def test_handler_failure_updates_job_and_attempt_in_one_transaction(
    failure_code: FailureCode,
    expects_retry: bool,
) -> None:
    now = datetime.now(UTC)
    job_id = uuid4()
    event_id = uuid4()
    lease_token = uuid4().hex
    lease_expires_at = now + timedelta(seconds=30)

    async with session_factory.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO ai_job (
                    id, job_type, status, expected_event_id,
                    attempt_count, max_attempts, available_at,
                    lease_token, lease_expires_at, heartbeat_at
                ) VALUES (
                    :job_id, 'OCR', 'PROCESSING', :event_id,
                    1, 3, :now, :lease_token, :lease_expires_at, :now
                )
                """
            ),
            {
                "job_id": str(job_id),
                "event_id": str(event_id),
                "now": now,
                "lease_token": lease_token,
                "lease_expires_at": lease_expires_at,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO ai_job_attempt (
                    id, ai_job_id, attempt_no, attempt_status,
                    retryable, timed_out, started_at
                ) VALUES (
                    :attempt_id, :job_id, 1, 'PROCESSING', false, false, :now
                )
                """
            ),
            {
                "attempt_id": str(uuid4()),
                "job_id": str(job_id),
                "now": now,
            },
        )

    retry_at = now + timedelta(seconds=5) if expects_retry else None
    lease = ExecutionLease(
        job_id=job_id,
        event_id=event_id,
        attempt=1,
        max_attempts=3,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
    )
    async with session_factory.begin() as session:
        recorded = await SqlAlchemyJobExecutionRepository(session).record_failure(
            lease,
            failure_code=failure_code,
            failed_at=now,
            retry_at=retry_at,
        )

    assert recorded is True
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT j.status, j.failure_code, j.expected_event_id,
                           j.last_consumed_event_id, j.available_at,
                           a.attempt_status, a.error_code, a.retryable, a.timed_out
                    FROM ai_job AS j
                    JOIN ai_job_attempt AS a ON a.ai_job_id = j.id
                    WHERE j.id = :job_id
                    """
                ),
                {"job_id": str(job_id)},
            )
        ).one()

    assert row.status == ("RETRY_WAIT" if expects_retry else "FAILED")
    assert row.failure_code == (None if expects_retry else failure_code)
    assert row.expected_event_id is None
    assert row.last_consumed_event_id == str(event_id)
    assert row.available_at == (retry_at if expects_retry else now)
    assert row.attempt_status == "FAILED"
    assert row.error_code == failure_code
    assert row.retryable is expects_retry
    assert row.timed_out is (failure_code == "TIMEOUT")


@pytest.mark.asyncio
async def test_retry_outbox_preserves_envelope_and_publishes_valid_message() -> None:
    now = datetime.now(UTC)
    job_id = uuid4()
    previous_event_id = uuid4()
    domain_id = uuid4()
    trace_id = uuid4().hex

    async with session_factory.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO ai_job (
                    id, job_type, status, expected_event_id,
                    last_consumed_event_id, attempt_count, max_attempts, available_at
                ) VALUES (
                    :job_id, 'OCR', 'RETRY_WAIT', NULL,
                    :event_id, 1, 3, :now
                )
                """
            ),
            {
                "job_id": str(job_id),
                "event_id": str(previous_event_id),
                "now": now,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO outbox_event (
                    event_id, job_id, attempt, event_kind, schema_version,
                    status, available_at, trace_id, domain_type, domain_id
                ) VALUES (
                    :event_id, :job_id, 1, 'JOB_EXECUTE', '1.0',
                    'PUBLISHED', :now, :trace_id, 'OCR_JOB', :domain_id
                )
                """
            ),
            {
                "event_id": str(previous_event_id),
                "job_id": str(job_id),
                "now": now,
                "trace_id": trace_id,
                "domain_id": str(domain_id),
            },
        )

    async with session_factory.begin() as session:
        scheduled = await SqlAlchemyRecoveryRepository(session).schedule_due_retries(
            now=now,
            limit=1,
        )

    assert len(scheduled) == 1
    stream_name = f"oryak:test:recovery:{uuid4().hex}"
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
        claim_token_factory=lambda: "retry-publisher",
    )

    try:
        await redis_client.ping()
        published = await publisher.publish_batch()
        assert len(published) == 1
        assert published[0].status is OutboxPublishStatus.PUBLISHED

        await stream.ensure_consumer_group()
        deliveries = await stream.read(consumer_name="worker", block_ms=100)
        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert isinstance(delivery, WorkerDelivery)
        assert delivery.message.event_id == scheduled[0].event_id
        assert delivery.message.job_id == job_id
        assert delivery.message.attempt == 2
        assert delivery.message.trace_id == trace_id
        assert delivery.message.domain_type.value == "OCR_JOB"
        assert delivery.message.domain_id == domain_id
    finally:
        await redis_client.delete(stream_name)
        await redis_client.aclose()
