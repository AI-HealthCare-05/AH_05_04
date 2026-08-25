import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import URL, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_test_database_url() -> URL:
    """CI 또는 로컬 PostgreSQL test DB의 접속 주소를 생성합니다."""
    return URL.create(
        drivername="postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host="127.0.0.1",
        port=config.DB_EXPOSE_PORT,
        database="test",
    )


@pytest_asyncio.fixture(scope="session")
async def migrated_engine() -> AsyncIterator[AsyncEngine]:
    """Alembic migration이 적용된 test DB에 연결합니다."""
    engine = create_async_engine(
        create_test_database_url(),
        pool_pre_ping=True,
        poolclass=NullPool,
    )

    yield engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_is_at_alembic_head(
    migrated_engine: AsyncEngine,
) -> None:
    """DB에 적용된 revision이 저장소의 Alembic head와 일치하는지 확인합니다."""
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    script_directory = ScriptDirectory.from_config(alembic_config)
    expected_heads = set(script_directory.get_heads())

    async with migrated_engine.connect() as connection:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        applied_revisions = set(result.scalars().all())

    assert applied_revisions == expected_heads


@pytest.mark.asyncio
async def test_ocr_created_sequence_uses_postgresql_identity(
    migrated_engine: AsyncEngine,
) -> None:
    """OCR 정렬용 sequence가 실제 PostgreSQL identity로 생성됐는지 검증합니다."""
    async with migrated_engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT
                    data_type,
                    is_nullable,
                    is_identity,
                    identity_generation
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ocr_job'
                  AND column_name = 'created_sequence'
                """
            )
        )
        column = result.mappings().one()

    assert column["data_type"] == "bigint"
    assert column["is_nullable"] == "NO"
    assert column["is_identity"] == "YES"
    assert column["identity_generation"] == "BY DEFAULT"


@pytest.mark.asyncio
async def test_ocr_created_sequence_index_exists(
    migrated_engine: AsyncEngine,
) -> None:
    """OCR 최신 결과 정렬에 필요한 복합 인덱스가 존재하는지 확인합니다."""
    async with migrated_engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'ocr_job'
                  AND indexname = 'idx_ocr_document_created_seq'
                """
            )
        )
        index_definition = result.scalar_one()

    assert "document_id" in index_definition
    assert "created_at" in index_definition
    assert "created_sequence" in index_definition


@pytest.mark.asyncio
async def test_user_email_unique_index_exists(
    migrated_engine: AsyncEngine,
) -> None:
    """사용자 이메일의 PostgreSQL unique index가 존재하는지 확인합니다."""
    async with migrated_engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'user'
                  AND indexname = 'ix_user_email'
                """
            )
        )
        index_definition = result.scalar_one()

    assert "CREATE UNIQUE INDEX" in index_definition
    assert "(email)" in index_definition


@pytest.mark.asyncio
async def test_concurrent_user_email_insert_allows_only_one(
    migrated_engine: AsyncEngine,
) -> None:
    """동일 이메일의 동시 INSERT에서 한 요청만 성공하는지 검증합니다."""
    email = f"migration-{uuid4().hex[:16]}@test.local"

    async def insert_user() -> bool:
        try:
            async with migrated_engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO "user" (
                            id,
                            email,
                            hashed_password,
                            name,
                            is_active,
                            is_admin
                        )
                        VALUES (
                            :id,
                            :email,
                            :hashed_password,
                            :name,
                            true,
                            false
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "email": email,
                        "hashed_password": "migration-test-password-hash",
                        "name": "migration-test",
                    },
                )
            return True
        except IntegrityError:
            return False

    try:
        results = await asyncio.gather(
            insert_user(),
            insert_user(),
        )

        assert sorted(results) == [False, True]
    finally:
        # 성공한 테스트 행을 삭제해 반복 실행 시에도 DB가 깨끗하게 유지되도록 합니다.
        async with migrated_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    DELETE FROM "user"
                    WHERE email = :email
                    """
                ),
                {"email": email},
            )
