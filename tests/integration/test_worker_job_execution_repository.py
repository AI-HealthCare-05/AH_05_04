"""실제 PostgreSQL에서 Worker Job lease 경합을 검증합니다."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from uuid import uuid4

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
from ai_worker.adapters.sqlalchemy_job_execution_repository import (
    SqlAlchemyJobExecutionRepository,
)
from ai_worker.adapters.sqlalchemy_lease_heartbeat import (
    SqlAlchemyLeaseHeartbeat,
)
from ai_worker.adapters.sqlalchemy_ocr_execution_starter import (
    SqlAlchemyOcrExecutionStarter,
)
from ai_worker.adapters.sqlalchemy_transaction import (
    SqlAlchemyTransaction,
)
from ai_worker.core.config import Config as WorkerConfig
from ai_worker.core.consumer_execution import LeaseAwareConsumerExecution
from ai_worker.core.dispatcher import Dispatcher
from ai_worker.core.job_execution import (
    CommittedDelivery,
    ExecutionLease,
    LeaseAcquisitionResult,
    LeaseNotAcquired,
)
from ai_worker.core.registry import HandlerRegistry
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.runtime_assembly import build_worker_runtime
from ai_worker.core.stream import WorkerDelivery
from ai_worker.schemas.messages import JobType, WorkerMessage
from provider_contracts.observability import DeploymentEnvironment
from provider_contracts.ocr import (
    OcrDeadline,
    OcrRecognitionResult,
    RecognizedField,
)

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
    connect_args={
        "server_settings": {
            "search_path": TEST_SCHEMA,
        }
    },
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


class BlockingHandler:
    """lease commit 이후 Provider 실행 구간을 재현합니다."""

    handler_type = JobType.OCR

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def handle(
        self,
        message: WorkerMessage,
    ) -> HandlerSuccess:
        self.started.set()
        await self.release.wait()

        return HandlerSuccess(
            event_id=message.event_id,
            job_id=message.job_id,
            handler_type=self.handler_type,
        )


class NoopResultStore:
    def __init__(self) -> None:
        self.save_count = 0

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        self.save_count += 1


class RecordingAcknowledger:
    def __init__(self) -> None:
        self.acknowledged_ids: list[str] = []

    async def acknowledge(
        self,
        stream_message_id: str,
    ) -> None:
        self.acknowledged_ids.append(stream_message_id)


class RetainedHeartbeatHandle:
    def __init__(self) -> None:
        self._stopped = asyncio.Event()

    async def wait(self) -> bool:
        await self._stopped.wait()
        return True

    async def stop(self) -> bool:
        self._stopped.set()
        return True


class RetainedHeartbeat:
    async def start(
        self,
        lease: ExecutionLease,
    ) -> RetainedHeartbeatHandle:
        return RetainedHeartbeatHandle()


class SyntheticOcrEngine:
    """실제 Redis·DB 경계를 유지하고 외부 CLOVA 호출만 대체합니다."""

    def __init__(self) -> None:
        self.call_count = 0

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        _ = object_key, file_mime_type, deadline
        self.call_count += 1

        return OcrRecognitionResult(
            fields=[
                RecognizedField(
                    medication_index=1,
                    field_type="MEDICATION_NAME",
                    raw_value="합성 의약품",
                    confidence_score=0.98,
                )
            ],
            engine_name="SYNTHETIC_OCR",
            model_version=None,
            prompt_version=None,
        )


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
                CREATE TABLE medical_document (
                    id VARCHAR(36) PRIMARY KEY,
                    object_key VARCHAR(500) NOT NULL,
                    file_mime_type VARCHAR(100) NOT NULL
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE ocr_job (
                    id VARCHAR(36) PRIMARY KEY,
                    document_id VARCHAR(36) NOT NULL,
                    ai_job_id VARCHAR(36) NOT NULL,
                    ocr_status VARCHAR(20) NOT NULL,
                    engine_name VARCHAR(100),
                    model_version VARCHAR(100),
                    prompt_version VARCHAR(100),
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    error_code VARCHAR(100),
                    error_message VARCHAR(500)
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                CREATE TABLE extracted_field (
                    id VARCHAR(36) PRIMARY KEY,
                    ocr_job_id VARCHAR(36) NOT NULL,
                    medication_index INTEGER NOT NULL,
                    field_type VARCHAR(30) NOT NULL,
                    raw_value VARCHAR(1000),
                    confidence_score NUMERIC(5, 4),
                    normalized_value VARCHAR(1000),
                    normalization_version VARCHAR(30),
                    confirmed_value VARCHAR(1000),
                    confirmation_status VARCHAR(20) NOT NULL,
                    confirmed_at TIMESTAMPTZ
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


async def test_committed_event_redelivery_keeps_single_attempt() -> None:
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

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as session:
        await use_test_schema(session)
        repository = SqlAlchemyJobExecutionRepository(session)

        acquired = await repository.acquire_lease(
            message,
            now=now,
            lease_duration=timedelta(seconds=30),
        )
        assert isinstance(acquired, ExecutionLease)

        completed = await repository.complete_execution(
            acquired,
            completed_at=now + timedelta(seconds=1),
        )
        assert completed is True
        await session.commit()

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as session:
        await use_test_schema(session)
        repository = SqlAlchemyJobExecutionRepository(session)

        redelivery = await repository.acquire_lease(
            message,
            now=now + timedelta(seconds=2),
            lease_duration=timedelta(seconds=30),
        )
        await session.commit()

    assert isinstance(redelivery, CommittedDelivery)

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as session:
        await use_test_schema(session)
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

    assert attempt_result.scalar_one() == 1


async def test_handler_runs_after_visible_lease_commit_without_job_row_lock() -> None:
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
        await connection.execute(
            text(
                """
                INSERT INTO medical_document (
                    id,
                    object_key,
                    file_mime_type
                )
                VALUES (:document_id,
                        'synthetic/input.png',
                        'image/png')
                """
            ),
            {"document_id": str(message.domain_id)},
        )
        await connection.execute(
            text(
                """
                INSERT INTO ocr_job (
                    id,
                    document_id,
                    ai_job_id,
                    ocr_status
                )
                VALUES (:domain_id,
                        :document_id,
                        :job_id,
                        'PENDING')
                """
            ),
            {
                "domain_id": str(message.domain_id),
                "document_id": str(message.domain_id),
                "job_id": str(message.job_id),
            },
        )

    async with AsyncSession(
        bind=test_engine,
        expire_on_commit=False,
    ) as worker_session:
        await use_test_schema(worker_session)

        handler = BlockingHandler()
        registry = HandlerRegistry()
        registry.register(handler)
        acknowledger = RecordingAcknowledger()

        execution = LeaseAwareConsumerExecution(
            dispatcher=Dispatcher(registry),
            result_store=NoopResultStore(),
            transaction=SqlAlchemyTransaction(worker_session),
            acknowledger=acknowledger,
            job_repository=SqlAlchemyJobExecutionRepository(worker_session),
            heartbeat=RetainedHeartbeat(),
            lease_duration=timedelta(seconds=30),
            clock=lambda: now,
            execution_starter=SqlAlchemyOcrExecutionStarter(
                worker_session,
            ),
        )

        execution_task = asyncio.create_task(
            execution.execute(
                WorkerDelivery(
                    stream_message_id="3000-0",
                    message=message,
                )
            )
        )

        await asyncio.wait_for(
            handler.started.wait(),
            timeout=1,
        )

        try:
            async with AsyncSession(
                bind=test_engine,
                expire_on_commit=False,
            ) as observer_session:
                await use_test_schema(observer_session)

                job_result = await observer_session.execute(
                    text(
                        """
                        SELECT
                            status,
                            attempt_count,
                            lease_token
                        FROM ai_job
                        WHERE id = :job_id
                        """
                    ),
                    {"job_id": str(message.job_id)},
                )
                status, attempt_count, lease_token = job_result.one()
                ocr_result = await observer_session.execute(
                    text(
                        """
                        SELECT
                            ocr_status,
                            started_at
                        FROM ocr_job
                        WHERE id = :domain_id
                        """
                    ),
                    {"domain_id": str(message.domain_id)},
                )
                ocr_status, ocr_started_at = ocr_result.one()
                assert status == "PROCESSING"
                assert attempt_count == 1
                assert lease_token is not None
                assert ocr_status == "PROCESSING"
                assert ocr_started_at is not None

                # Handler가 대기 중이어도 lease 획득 transaction은
                # 이미 commit됐으므로 다른 transaction이 즉시 row lock을
                # 획득할 수 있어야 합니다.
                locked_result = await observer_session.execute(
                    text(
                        """
                        SELECT id
                        FROM ai_job
                        WHERE id = :job_id
                        FOR UPDATE NOWAIT
                        """
                    ),
                    {"job_id": str(message.job_id)},
                )
                locked_ocr_result = await observer_session.execute(
                    text(
                        """
                        SELECT id
                        FROM ocr_job
                        WHERE id = :domain_id
                        FOR UPDATE NOWAIT
                        """
                    ),
                    {"domain_id": str(message.domain_id)},
                )
                assert locked_ocr_result.scalar_one() == str(message.domain_id)
                assert locked_result.scalar_one() == str(message.job_id)
                await observer_session.rollback()
        finally:
            handler.release.set()
            result = await execution_task

    assert isinstance(result, HandlerSuccess)
    assert acknowledger.acknowledged_ids == ["3000-0"]


async def test_postgresql_heartbeat_loss_blocks_result_and_ack() -> None:
    message = build_message()
    now = datetime.now(UTC)

    async with test_engine.begin() as connection:
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

    heartbeat_session_factory = async_sessionmaker(
        test_engine,
        expire_on_commit=False,
    )

    async with heartbeat_session_factory() as worker_session:
        handler = BlockingHandler()
        registry = HandlerRegistry()
        registry.register(handler)
        result_store = NoopResultStore()
        acknowledger = RecordingAcknowledger()

        execution = LeaseAwareConsumerExecution(
            dispatcher=Dispatcher(registry),
            result_store=result_store,
            transaction=SqlAlchemyTransaction(worker_session),
            acknowledger=acknowledger,
            job_repository=SqlAlchemyJobExecutionRepository(worker_session),
            heartbeat=SqlAlchemyLeaseHeartbeat(
                session_factory=heartbeat_session_factory,
                lease_duration=timedelta(seconds=5),
                heartbeat_interval=timedelta(milliseconds=10),
                clock=lambda: datetime.now(UTC),
            ),
            lease_duration=timedelta(seconds=5),
            clock=lambda: datetime.now(UTC),
        )

        execution_task = asyncio.create_task(
            execution.execute(
                WorkerDelivery(
                    stream_message_id="3001-0",
                    message=message,
                )
            )
        )

        await asyncio.wait_for(
            handler.started.wait(),
            timeout=1,
        )

        try:
            # 별도 Worker가 소유권을 가져간 상황을 재현합니다.
            async with heartbeat_session_factory() as fencing_session:
                await fencing_session.execute(
                    text(
                        """
                        UPDATE ai_job
                        SET lease_token = :new_lease_token
                        WHERE id = :job_id
                        """
                    ),
                    {
                        "new_lease_token": uuid4().hex,
                        "job_id": str(message.job_id),
                    },
                )
                await fencing_session.commit()

            # heartbeat가 다음 주기에 조건부 UPDATE 0건을 관찰하도록 기다립니다.
            await asyncio.sleep(0.05)
        finally:
            handler.release.set()
            result = await execution_task

    assert isinstance(result, LeaseNotAcquired)
    assert result_store.save_count == 0
    assert acknowledger.acknowledged_ids == []


async def test_worker_runtime_completes_real_redis_postgresql_ocr_one_cycle(
    tmp_path: Path,
) -> None:
    message = build_message()
    now = datetime.now(UTC)
    document_id = uuid4()
    stream_name = f"oryak:runtime-test:{uuid4().hex}"
    group_name = f"runtime-workers-{uuid4().hex}"

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
    ocr_engine = SyntheticOcrEngine()
    worker_config = WorkerConfig(  # type: ignore[call-arg]
        _env_file=None,
        ENV=DeploymentEnvironment.LOCAL,
        DB_HOST="127.0.0.1",
        DB_NAME="test",
        DB_USER="worker",
        DB_PASSWORD="synthetic-password",
        REDIS_STREAM_NAME=stream_name,
        REDIS_CONSUMER_GROUP=group_name,
        REDIS_CONSUMER_NAME="runtime-worker-1",
        REDIS_BLOCK_MS=100,
        CLOVA_OCR_INVOKE_URL="https://clova.test/ocr",
        CLOVA_OCR_SECRET="synthetic-clova-secret",
        STORAGE_DIR=str(tmp_path),
    )

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
        await connection.execute(
            text(
                """
                INSERT INTO medical_document (
                    id,
                    object_key,
                    file_mime_type
                )
                VALUES (
                    :document_id,
                    'synthetic/input.png',
                    'image/png'
                )
                """
            ),
            {"document_id": str(document_id)},
        )
        await connection.execute(
            text(
                """
                INSERT INTO ocr_job (
                    id,
                    document_id,
                    ai_job_id,
                    ocr_status
                )
                VALUES (
                    :domain_id,
                    :document_id,
                    :job_id,
                    'PENDING'
                )
                """
            ),
            {
                "domain_id": str(message.domain_id),
                "document_id": str(document_id),
                "job_id": str(message.job_id),
            },
        )

    assembled = build_worker_runtime(
        worker_config,
        logger=__import__("logging").getLogger("worker-runtime-test"),
        clock=lambda: datetime.now(UTC),
        ocr_engine=ocr_engine,
        redis_client=redis_client,
        engine=test_engine,
    )

    try:
        await assembled.runtime.initialize()
        stream_message_id = await stream.publish(message)

        processed_count = await assembled.runtime.run_once()

        async with AsyncSession(
            bind=test_engine,
            expire_on_commit=False,
        ) as observer:
            await use_test_schema(observer)

            job_result = await observer.execute(
                text(
                    """
                    SELECT
                        status,
                        attempt_count,
                        last_consumed_event_id,
                        completed_at
                    FROM ai_job
                    WHERE id = :job_id
                    """
                ),
                {"job_id": str(message.job_id)},
            )
            job_status, attempt_count, consumed_event_id, job_completed_at = job_result.one()

            ocr_result = await observer.execute(
                text(
                    """
                    SELECT
                        ocr_status,
                        started_at,
                        completed_at,
                        engine_name
                    FROM ocr_job
                    WHERE id = :domain_id
                    """
                ),
                {"domain_id": str(message.domain_id)},
            )
            ocr_status, ocr_started_at, ocr_completed_at, engine_name = ocr_result.one()

            field_count_result = await observer.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM extracted_field
                    WHERE ocr_job_id = :domain_id
                    """
                ),
                {"domain_id": str(message.domain_id)},
            )

        assert processed_count == 1
        assert ocr_engine.call_count == 1
        assert job_status == "COMPLETED"
        assert attempt_count == 1
        assert consumed_event_id == str(message.event_id)
        assert job_completed_at is not None
        assert ocr_status == "COMPLETED"
        assert ocr_started_at is not None
        assert ocr_completed_at is not None
        assert engine_name == "SYNTHETIC_OCR"
        assert field_count_result.scalar_one() == 1
        assert await stream.list_pending() == ()
        assert stream_message_id
    finally:
        await assembled.aclose()
        await redis_client.delete(stream_name)
        await redis_client.aclose()
