"""실제 PostgreSQL에서 OCR Worker 입력 조회와 결과 저장을 검증합니다."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from importlib import import_module
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from ai_worker.adapters.sqlalchemy_ocr_input_repository import (
    SqlAlchemyOcrInputRepository,
)
from ai_worker.adapters.sqlalchemy_ocr_result_store import (
    SqlAlchemyOcrResultStore,
)
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tasks.ocr.handler import (
    OcrDomainInput,
    OcrHandlerSuccess,
    OcrRecognizedField,
)

app_core = import_module("app.core")
config = app_core.config

TEST_SCHEMA = "worker_ocr_persistence_test"

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

session_factory = async_sessionmaker(
    test_engine,
    expire_on_commit=False,
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
                    id VARCHAR(36) PRIMARY KEY
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
                    ai_job_id VARCHAR(36),
                    ocr_status VARCHAR(20) NOT NULL,
                    engine_name VARCHAR(100),
                    model_version VARCHAR(100),
                    prompt_version VARCHAR(100),
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

    yield

    async with test_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))

    await test_engine.dispose()


def build_message(
    *,
    job_id: UUID,
    domain_id: UUID,
) -> WorkerMessage:
    now = datetime.now(UTC)

    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(job_id),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(domain_id),
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": uuid4().hex,
        }
    )


async def read_persisted_result(
    *,
    domain_id: UUID,
) -> tuple[str, int]:
    async with session_factory() as observer:
        status_result = await observer.execute(
            text(
                """
                SELECT ocr_status
                FROM ocr_job
                WHERE id = :domain_id
                """
            ),
            {"domain_id": str(domain_id)},
        )
        field_result = await observer.execute(
            text(
                """
                SELECT COUNT(*)
                FROM extracted_field
                WHERE ocr_job_id = :domain_id
                """
            ),
            {"domain_id": str(domain_id)},
        )

        return (
            status_result.scalar_one(),
            field_result.scalar_one(),
        )


async def test_ocr_input_and_result_share_one_external_transaction() -> None:
    job_id = uuid4()
    domain_id = uuid4()
    document_id = uuid4()
    message = build_message(
        job_id=job_id,
        domain_id=domain_id,
    )

    async with test_engine.begin() as connection:
        await connection.execute(text(f"SET search_path TO {TEST_SCHEMA}"))
        await connection.execute(
            text("INSERT INTO ai_job (id) VALUES (:job_id)"),
            {"job_id": str(job_id)},
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
                "domain_id": str(domain_id),
                "document_id": str(document_id),
                "job_id": str(job_id),
            },
        )

    async with session_factory() as session:
        input_repository = SqlAlchemyOcrInputRepository(session)
        result_store = SqlAlchemyOcrResultStore(
            session,
            clock=lambda: datetime.now(UTC),
        )

        domain_input = await input_repository.get_input(
            domain_id=domain_id,
            job_id=job_id,
        )

        assert domain_input == OcrDomainInput(
            object_key="synthetic/input.png",
            file_mime_type="image/png",
        )

        await result_store.save(
            message=message,
            result=OcrHandlerSuccess(
                event_id=message.event_id,
                job_id=message.job_id,
                handler_type=JobType.OCR,
                domain_id=message.domain_id,
                fields=(
                    OcrRecognizedField(
                        medication_index=1,
                        field_type="MEDICATION_NAME",
                        raw_value="합성 의약품",
                        confidence_score=0.98,
                        normalized_value=None,
                        normalization_version=None,
                    ),
                ),
                engine_name="CLOVA_OCR",
                model_version=None,
                prompt_version=None,
            ),
        )

        # ResultStore가 직접 commit하지 않았으므로 외부 session에는
        # 아직 변경 결과가 보여서는 안 됩니다.
        assert await read_persisted_result(domain_id=domain_id) == ("PENDING", 0)

        await session.commit()

    assert await read_persisted_result(domain_id=domain_id) == ("COMPLETED", 1)
