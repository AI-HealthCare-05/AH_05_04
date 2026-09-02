"""실제 PostgreSQL에서 Worker Job lease 경합을 검증합니다."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from ai_worker.adapters.sqlalchemy_job_execution_repository import (
    SqlAlchemyJobExecutionRepository,
)
from ai_worker.core.job_execution import (
    ExecutionLease,
    LeaseAcquisitionResult,
    LeaseNotAcquired,
)
from ai_worker.schemas.messages import WorkerMessage

app_core = import_module("app.core")
config = app_core.config

TEST_SCHEMA = "worker_job_repository_test"

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
)


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
                CREATE TABLE ai_job (
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
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE outbox_event (
                    event_id VARCHAR(36) PRIMARY KEY,
                    job_id VARCHAR(36) NOT NULL,
                    attempt INTEGER NOT NULL,
                    event_kind VARCHAR(30) NOT NULL
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE ai_job_attempt (
                    id VARCHAR(36) PRIMARY KEY,
                    ai_job_id VARCHAR(36) NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    attempt_status VARCHAR(30) NOT NULL,
                    retryable BOOLEAN NOT NULL,
                    timed_out BOOLEAN NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    UNIQUE (ai_job_id, attempt_no)
                )
                """
            )
        )

    yield

    async with test_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))

    await test_engine.dispose()


async def test_only_one_worker_acquires_same_job_lease() -> None:
    message = build_message()
    now = datetime.now(UTC)

    async with test_engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {TEST_SCHEMA}"))
        await connection.execute(
            text(
                """
                INSERT INTO ai_job (
                    id,
                    job_type,
                    status,
                    expected_event_id,
                    attempt_count,
                    max_attempts,
                    available_at
                )
                VALUES (
                    :job_id,
                    'OCR',
                    'PENDING',
                    :event_id,
                    0,
                    3,
                    :available_at
                )
                """
            ),
            {
                "job_id": str(message.job_id),
                "event_id": str(message.event_id),
                "available_at": now,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO outbox_event (
                    event_id,
                    job_id,
                    attempt,
                    event_kind
                )
                VALUES (
                    :event_id,
                    :job_id,
                    :attempt,
                    'JOB_EXECUTE'
                )
                """
            ),
            {
                "event_id": str(message.event_id),
                "job_id": str(message.job_id),
                "attempt": message.attempt,
            },
        )

    barrier = asyncio.Barrier(2)

    async def contend() -> LeaseAcquisitionResult:
        async with AsyncSession(
            bind=test_engine,
            expire_on_commit=False,
        ) as session:
            await use_test_schema(session)
            await barrier.wait()

            repository = SqlAlchemyJobExecutionRepository(session)
            result = await repository.acquire_lease(
                message,
                now=now,
                lease_duration=timedelta(seconds=30),
            )
            await session.commit()

            return result

    first, second = await asyncio.gather(
        contend(),
        contend(),
    )

    results = (first, second)

    assert sum(isinstance(result, ExecutionLease) for result in results) == 1
    assert sum(isinstance(result, LeaseNotAcquired) for result in results) == 1

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as session:
        await use_test_schema(session)

        job_result = await session.execute(
            text(
                """
                SELECT status, attempt_count
                FROM ai_job
                WHERE id = :job_id
                """
            ),
            {"job_id": str(message.job_id)},
        )
        status, attempt_count = job_result.one()

        attempt_result = await session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM ai_job_attempt
                WHERE ai_job_id = :job_id
                """
            ),
            {"job_id": str(message.job_id)},
        )

    assert status == "PROCESSING"
    assert attempt_count == 1
    assert attempt_result.scalar_one() == 1
