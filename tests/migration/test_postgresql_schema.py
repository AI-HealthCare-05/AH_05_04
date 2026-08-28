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
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
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


async def insert_ocr_parent_chain(
    connection: AsyncConnection,
) -> tuple[str, str, str]:
    """extracted_field 제약조건 테스트에 필요한 최소 부모 데이터를 생성합니다."""
    user_id = str(uuid4())
    document_id = str(uuid4())
    ocr_job_id = str(uuid4())

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
            "id": user_id,
            "email": f"constraint-{uuid4().hex[:12]}@test.local",
            "hashed_password": "migration-test-password-hash",
            "name": "constraint-test",
        },
    )

    await connection.execute(
        text(
            """
            INSERT INTO medical_document (
                id,
                user_id,
                document_type,
                original_file_name,
                object_key,
                file_mime_type,
                file_size_bytes,
                upload_status
            )
            VALUES (
                :id,
                :user_id,
                'PRESCRIPTION',
                'constraint-test.png',
                :object_key,
                'image/png',
                1,
                'UPLOADED'
            )
            """
        ),
        {
            "id": document_id,
            "user_id": user_id,
            "object_key": f"migration-test/{document_id}.png",
        },
    )

    await connection.execute(
        text(
            """
            INSERT INTO ocr_job (
                id,
                document_id,
                ocr_status
            )
            VALUES (
                :id,
                :document_id,
                'PENDING'
            )
            """
        ),
        {
            "id": ocr_job_id,
            "document_id": document_id,
        },
    )

    return user_id, document_id, ocr_job_id


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
    alembic_config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
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
async def test_ocr_created_sequence_index_has_expected_order(
    migrated_engine: AsyncEngine,
) -> None:
    """public schema의 OCR 복합 인덱스 컬럼 순서를 검증합니다."""
    async with migrated_engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT array_agg(
                    attribute.attname
                    ORDER BY indexed_column.ordinality
                )
                FROM pg_class AS table_info
                JOIN pg_index AS index_info
                  ON index_info.indrelid = table_info.oid
                JOIN pg_class AS index_class
                  ON index_class.oid = index_info.indexrelid
                JOIN pg_namespace AS table_namespace
                  ON table_namespace.oid = table_info.relnamespace
                JOIN pg_namespace AS index_namespace
                  ON index_namespace.oid = index_class.relnamespace
                CROSS JOIN LATERAL
                    unnest(index_info.indkey)
                    WITH ORDINALITY AS indexed_column(
                        attribute_number,
                        ordinality
                    )
                JOIN pg_attribute AS attribute
                  ON attribute.attrelid = table_info.oid
                 AND attribute.attnum = indexed_column.attribute_number
                WHERE table_namespace.nspname = 'public'
                  AND index_namespace.nspname = 'public'
                  AND table_info.relname = 'ocr_job'
                  AND index_class.relname = 'idx_ocr_document_created_seq'
                  AND indexed_column.ordinality <= index_info.indnkeyatts
                """
            )
        )
        index_columns = result.scalar_one()

    assert index_columns == [
        "document_id",
        "created_at",
        "created_sequence",
    ]


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


@pytest.mark.asyncio
async def test_confirmed_optional_ocr_field_allows_null(
    migrated_engine: AsyncEngine,
) -> None:
    """선택 OCR 필드는 CONFIRMED 상태에서 confirmed_value=null을 허용합니다."""
    async with migrated_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            _, _, ocr_job_id = await insert_ocr_parent_chain(connection)

            field_id = str(uuid4())

            await connection.execute(
                text(
                    """
                    INSERT INTO extracted_field (
                        id,
                        ocr_job_id,
                        medication_index,
                        field_type,
                        raw_value,
                        confirmed_value,
                        confirmation_status,
                        confirmed_at
                    )
                    VALUES (
                        :id,
                        :ocr_job_id,
                        1,
                        'MEDICATION_STRENGTH',
                        '100mg',
                        NULL,
                        'CONFIRMED',
                        now()
                    )
                    """
                ),
                {
                    "id": field_id,
                    "ocr_job_id": ocr_job_id,
                },
            )

            result = await connection.execute(
                text(
                    """
                    SELECT
                        raw_value,
                        confirmed_value,
                        confirmation_status,
                        confirmed_at
                    FROM extracted_field
                    WHERE id = :id
                    """
                ),
                {"id": field_id},
            )
            stored = result.mappings().one()

            assert stored["raw_value"] == "100mg"
            assert stored["confirmed_value"] is None
            assert stored["confirmation_status"] == "CONFIRMED"
            assert stored["confirmed_at"] is not None
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_confirmed_required_ocr_field_rejects_null(
    migrated_engine: AsyncEngine,
) -> None:
    """필수 OCR 필드는 CONFIRMED 상태에서 confirmed_value=null을 거부합니다."""
    async with migrated_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            _, _, ocr_job_id = await insert_ocr_parent_chain(connection)

            with pytest.raises(
                IntegrityError,
                match="chk_field_confirmation_fields",
            ):
                await connection.execute(
                    text(
                        """
                        INSERT INTO extracted_field (
                            id,
                            ocr_job_id,
                            medication_index,
                            field_type,
                            raw_value,
                            confirmed_value,
                            confirmation_status,
                            confirmed_at
                        )
                        VALUES (
                            :id,
                            :ocr_job_id,
                            1,
                            'MEDICATION_NAME',
                            '합성의약품정',
                            NULL,
                            'CONFIRMED',
                            now()
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "ocr_job_id": ocr_job_id,
                    },
                )
        finally:
            await transaction.rollback()
