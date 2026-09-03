import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import URL, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRE_PROFILE_REVISION = "77585c0c9792"
PROFILE_EXPAND_REVISION = "117a8c9d4e21"
OCR_AI_JOB_BASE_REVISION = "8d4f1a6c9e2b"


def create_test_database_url() -> URL:
    """Alembic이 migration을 적용한 PostgreSQL test DB의 접속 주소를 생성합니다."""
    return URL.create(
        drivername="postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host="127.0.0.1",
        port=config.DB_PORT,
        database=config.DB_NAME,
    )


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


def create_alembic_database_url() -> str:
    return config.database_url


async def insert_ocr_parent_chain(
    connection: AsyncConnection,
) -> tuple[str, str, str]:
    """extracted_field 제약조건 테스트에 필요한 최소 부모 데이터를 생성합니다."""
    user_id = str(uuid4())
    profile_id = str(uuid4())
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
            INSERT INTO profile (
                id,
                user_id,
                profile_type,
                display_name
            )
            VALUES (
                :id,
                :user_id,
                'SELF',
                'constraint-test'
            )
            """
        ),
        {
            "id": profile_id,
            "user_id": user_id,
        },
    )

    await connection.execute(
        text(
            """
            INSERT INTO medical_document (
                id,
                uploaded_by,
                profile_id,
                document_type,
                original_file_name,
                object_key,
                file_mime_type,
                file_size_bytes,
                upload_status
            )
            VALUES (
                :id,
                :uploaded_by,
                :profile_id,
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
            "uploaded_by": user_id,
            "profile_id": profile_id,
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


async def _insert_legacy_profile_graph_data() -> tuple[str, str, str, str, str, str]:
    user_id = str(uuid4())
    document_id = str(uuid4())
    ocr_job_id = str(uuid4())
    prescription_id = str(uuid4())
    guide_id = str(uuid4())
    chat_session_id = str(uuid4())
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)

    try:
        async with engine.begin() as connection:
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
                    "email": f"p{uuid4().hex[:12]}@t.local",
                    "hashed_password": "migration-test-password-hash",
                    "name": "profile-roundtrip",
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
                        'profile-roundtrip.png',
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
                    "object_key": f"profile-roundtrip/{document_id}.png",
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
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription (
                        id,
                        document_id,
                        source_ocr_job_id,
                        prescribed_date,
                        prescription_status,
                        confirmed_at
                    )
                    VALUES (
                        :id,
                        :document_id,
                        :source_ocr_job_id,
                        DATE '2026-08-31',
                        'CONFIRMED',
                        now()
                    )
                    """
                ),
                {
                    "id": prescription_id,
                    "document_id": document_id,
                    "source_ocr_job_id": ocr_job_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO guide (
                        id,
                        prescription_id,
                        generation_status
                    )
                    VALUES (
                        :id,
                        :prescription_id,
                        'PENDING'
                    )
                    """
                ),
                {
                    "id": guide_id,
                    "prescription_id": prescription_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO chat_session (
                        id,
                        prescription_id,
                        session_status
                    )
                    VALUES (
                        :id,
                        :prescription_id,
                        'ACTIVE'
                    )
                    """
                ),
                {
                    "id": chat_session_id,
                    "prescription_id": prescription_id,
                },
            )
    finally:
        await engine.dispose()

    return user_id, document_id, ocr_job_id, prescription_id, guide_id, chat_session_id


async def _fetch_head_ocr_owner_chain(ocr_job_id: str) -> dict[str, object]:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT
                        ocr_job.id AS ocr_job_id,
                        medical_document.id AS document_id,
                        medical_document.uploaded_by,
                        medical_document.profile_id,
                        profile.user_id AS profile_user_id
                    FROM ocr_job
                    JOIN medical_document
                      ON medical_document.id = ocr_job.document_id
                    JOIN profile
                      ON profile.id = medical_document.profile_id
                    WHERE ocr_job.id = :ocr_job_id
                    """
                ),
                {"ocr_job_id": ocr_job_id},
            )
            return dict(result.mappings().one())
    finally:
        await engine.dispose()


async def _fetch_head_profile_graph(
    *,
    ocr_job_id: str,
    prescription_id: str,
    guide_id: str,
    chat_session_id: str,
) -> dict[str, object]:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT
                        ocr_job.id AS ocr_job_id,
                        medical_document.id AS document_id,
                        medical_document.uploaded_by,
                        medical_document.profile_id AS document_profile_id,
                        prescription.id AS prescription_id,
                        prescription.profile_id AS prescription_profile_id,
                        guide.id AS guide_id,
                        guide.profile_id AS guide_profile_id,
                        chat_session.id AS chat_session_id,
                        chat_session.profile_id AS chat_session_profile_id,
                        profile.user_id AS profile_user_id
                    FROM ocr_job
                    JOIN medical_document
                      ON medical_document.id = ocr_job.document_id
                    JOIN profile
                      ON profile.id = medical_document.profile_id
                    JOIN prescription
                      ON prescription.document_id = medical_document.id
                    JOIN guide
                      ON guide.prescription_id = prescription.id
                    JOIN chat_session
                      ON chat_session.prescription_id = prescription.id
                    WHERE ocr_job.id = :ocr_job_id
                      AND prescription.id = :prescription_id
                      AND guide.id = :guide_id
                      AND chat_session.id = :chat_session_id
                    """
                ),
                {
                    "ocr_job_id": ocr_job_id,
                    "prescription_id": prescription_id,
                    "guide_id": guide_id,
                    "chat_session_id": chat_session_id,
                },
            )
            return dict(result.mappings().one())
    finally:
        await engine.dispose()


async def _fetch_expand_revision_ocr_owner_chain(ocr_job_id: str) -> dict[str, object]:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT
                        ocr_job.id AS ocr_job_id,
                        medical_document.id AS document_id,
                        medical_document.user_id,
                        medical_document.profile_id,
                        profile.user_id AS profile_user_id
                    FROM ocr_job
                    JOIN medical_document
                      ON medical_document.id = ocr_job.document_id
                    JOIN profile
                      ON profile.id = medical_document.profile_id
                    WHERE ocr_job.id = :ocr_job_id
                    """
                ),
                {"ocr_job_id": ocr_job_id},
            )
            return dict(result.mappings().one())
    finally:
        await engine.dispose()


async def _fetch_medical_document_columns_and_index() -> tuple[set[str], bool]:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            columns_result = await connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'medical_document'
                      AND column_name IN ('user_id', 'uploaded_by')
                    """
                )
            )
            index_result = await connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'medical_document'
                          AND indexname = 'idx_document_user_uploaded'
                    )
                    """
                )
            )
            return set(columns_result.scalars().all()), bool(index_result.scalar_one())
    finally:
        await engine.dispose()


async def _fetch_profile_integrity_gap_counts() -> dict[str, int]:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT 'medical_document_missing' AS name, count(*) AS count
                    FROM medical_document
                    WHERE profile_id IS NULL
                    UNION ALL
                    SELECT 'medical_document_orphan' AS name, count(*) AS count
                    FROM medical_document
                    LEFT JOIN profile ON profile.id = medical_document.profile_id
                    WHERE profile.id IS NULL
                    UNION ALL
                    SELECT 'prescription_missing' AS name, count(*) AS count
                    FROM prescription
                    WHERE profile_id IS NULL
                    UNION ALL
                    SELECT 'prescription_orphan' AS name, count(*) AS count
                    FROM prescription
                    LEFT JOIN profile ON profile.id = prescription.profile_id
                    WHERE profile.id IS NULL
                    UNION ALL
                    SELECT 'guide_missing' AS name, count(*) AS count
                    FROM guide
                    WHERE profile_id IS NULL
                    UNION ALL
                    SELECT 'guide_orphan' AS name, count(*) AS count
                    FROM guide
                    LEFT JOIN profile ON profile.id = guide.profile_id
                    WHERE profile.id IS NULL
                    UNION ALL
                    SELECT 'chat_session_missing' AS name, count(*) AS count
                    FROM chat_session
                    WHERE profile_id IS NULL
                    UNION ALL
                    SELECT 'chat_session_orphan' AS name, count(*) AS count
                    FROM chat_session
                    LEFT JOIN profile ON profile.id = chat_session.profile_id
                    WHERE profile.id IS NULL
                    """
                )
            )
            return {str(row._mapping["name"]): int(row._mapping["count"]) for row in result}
    finally:
        await engine.dispose()


async def _cleanup_profile_roundtrip_data(
    *,
    user_id: str,
    document_id: str,
    ocr_job_id: str,
    prescription_id: str,
    guide_id: str,
    chat_session_id: str,
) -> None:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM chat_session WHERE id = :id"), {"id": chat_session_id})
            await connection.execute(text("DELETE FROM guide WHERE id = :id"), {"id": guide_id})
            await connection.execute(text("DELETE FROM prescription WHERE id = :id"), {"id": prescription_id})
            await connection.execute(text("DELETE FROM ocr_job WHERE id = :id"), {"id": ocr_job_id})
            await connection.execute(text("DELETE FROM medical_document WHERE id = :id"), {"id": document_id})
            await connection.execute(text("DELETE FROM profile WHERE user_id = :user_id"), {"user_id": user_id})
            await connection.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user_id})
    finally:
        await engine.dispose()


def test_profile_migration_preserves_existing_resource_graph_and_roundtrips() -> None:
    """기존 의료문서->OCR->처방->가이드/채팅 데이터가 PROFILE 전환 후 원 소유자에 연결되는지 검증합니다."""
    alembic_config = create_alembic_config()
    user_id = ""
    document_id = ""
    ocr_job_id = ""
    prescription_id = ""
    guide_id = ""
    chat_session_id = ""

    try:
        command.downgrade(alembic_config, PRE_PROFILE_REVISION)
        user_id, document_id, ocr_job_id, prescription_id, guide_id, chat_session_id = asyncio.run(
            _insert_legacy_profile_graph_data()
        )

        command.upgrade(alembic_config, "head")
        head_chain = asyncio.run(
            _fetch_head_profile_graph(
                ocr_job_id=ocr_job_id,
                prescription_id=prescription_id,
                guide_id=guide_id,
                chat_session_id=chat_session_id,
            )
        )
        gap_counts = asyncio.run(_fetch_profile_integrity_gap_counts())

        assert head_chain["ocr_job_id"] == ocr_job_id
        assert head_chain["document_id"] == document_id
        assert head_chain["uploaded_by"] == user_id
        assert head_chain["prescription_id"] == prescription_id
        assert head_chain["guide_id"] == guide_id
        assert head_chain["chat_session_id"] == chat_session_id
        assert head_chain["document_profile_id"] is not None
        assert head_chain["prescription_profile_id"] == head_chain["document_profile_id"]
        assert head_chain["guide_profile_id"] == head_chain["document_profile_id"]
        assert head_chain["chat_session_profile_id"] == head_chain["document_profile_id"]
        assert head_chain["profile_user_id"] == user_id
        assert set(gap_counts.values()) == {0}

        command.downgrade(alembic_config, PROFILE_EXPAND_REVISION)
        columns, has_user_index = asyncio.run(_fetch_medical_document_columns_and_index())
        assert columns == {"user_id"}
        assert has_user_index is True

        expand_chain = asyncio.run(_fetch_expand_revision_ocr_owner_chain(ocr_job_id))
        assert expand_chain["ocr_job_id"] == ocr_job_id
        assert expand_chain["document_id"] == document_id
        assert expand_chain["user_id"] == user_id
        assert expand_chain["profile_id"] is not None
        assert expand_chain["profile_user_id"] == user_id

        command.upgrade(alembic_config, "head")
        final_chain = asyncio.run(
            _fetch_head_profile_graph(
                ocr_job_id=ocr_job_id,
                prescription_id=prescription_id,
                guide_id=guide_id,
                chat_session_id=chat_session_id,
            )
        )
        final_gap_counts = asyncio.run(_fetch_profile_integrity_gap_counts())

        assert final_chain["ocr_job_id"] == ocr_job_id
        assert final_chain["document_id"] == document_id
        assert final_chain["uploaded_by"] == user_id
        assert final_chain["prescription_id"] == prescription_id
        assert final_chain["guide_id"] == guide_id
        assert final_chain["chat_session_id"] == chat_session_id
        assert final_chain["document_profile_id"] == head_chain["document_profile_id"]
        assert final_chain["prescription_profile_id"] == head_chain["document_profile_id"]
        assert final_chain["guide_profile_id"] == head_chain["document_profile_id"]
        assert final_chain["chat_session_profile_id"] == head_chain["document_profile_id"]
        assert final_chain["profile_user_id"] == user_id
        assert set(final_gap_counts.values()) == {0}
    finally:
        command.upgrade(alembic_config, "head")
        if user_id and document_id and ocr_job_id and prescription_id and guide_id and chat_session_id:
            asyncio.run(
                _cleanup_profile_roundtrip_data(
                    user_id=user_id,
                    document_id=document_id,
                    ocr_job_id=ocr_job_id,
                    prescription_id=prescription_id,
                    guide_id=guide_id,
                    chat_session_id=chat_session_id,
                )
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


async def _insert_ai_job_for_ocr_mapping(
    connection: AsyncConnection,
    *,
    user_id: str,
) -> str:
    ai_job_id = str(uuid4())

    await connection.execute(
        text(
            """
            INSERT INTO ai_job (
                id,
                user_id,
                job_type,
                status,
                attempt_count,
                max_attempts
            )
            VALUES (
                :id,
                :user_id,
                'OCR',
                'PENDING',
                0,
                3
            )
            """
        ),
        {
            "id": ai_job_id,
            "user_id": user_id,
        },
    )

    return ai_job_id


@pytest.mark.asyncio
async def test_ocr_ai_job_mapping_constraints_exist(
    migrated_engine: AsyncEngine,
) -> None:
    async with migrated_engine.connect() as connection:
        column_result = await connection.execute(
            text(
                """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ocr_job'
                  AND column_name = 'ai_job_id'
                """
            )
        )
        constraint_result = await connection.execute(
            text(
                """
                SELECT
                    conname,
                    pg_get_constraintdef(oid) AS definition
                FROM pg_constraint
                WHERE conrelid = 'ocr_job'::regclass
                  AND conname IN (
                      'fk_ocr_job_ai_job',
                      'uq_ocr_job_ai_job'
                  )
                """
            )
        )

    assert column_result.scalar_one() == "YES"

    constraints = {row._mapping["conname"]: row._mapping["definition"] for row in constraint_result}

    assert constraints["fk_ocr_job_ai_job"] == "FOREIGN KEY (ai_job_id) REFERENCES ai_job(id) ON DELETE SET NULL"
    assert constraints["uq_ocr_job_ai_job"] == "UNIQUE (ai_job_id)"


@pytest.mark.asyncio
async def test_ocr_ai_job_mapping_rejects_unknown_ai_job(
    migrated_engine: AsyncEngine,
) -> None:
    async with migrated_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            _, _, ocr_job_id = await insert_ocr_parent_chain(connection)

            with pytest.raises(
                IntegrityError,
                match="fk_ocr_job_ai_job",
            ):
                await connection.execute(
                    text(
                        """
                        UPDATE ocr_job
                        SET ai_job_id = :ai_job_id
                        WHERE id = :ocr_job_id
                        """
                    ),
                    {
                        "ai_job_id": str(uuid4()),
                        "ocr_job_id": ocr_job_id,
                    },
                )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_deleting_ai_job_sets_ocr_mapping_to_null(
    migrated_engine: AsyncEngine,
) -> None:
    async with migrated_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            user_id, _, ocr_job_id = await insert_ocr_parent_chain(connection)
            ai_job_id = await _insert_ai_job_for_ocr_mapping(
                connection,
                user_id=user_id,
            )

            await connection.execute(
                text(
                    """
                    UPDATE ocr_job
                    SET ai_job_id = :ai_job_id
                    WHERE id = :ocr_job_id
                    """
                ),
                {
                    "ai_job_id": ai_job_id,
                    "ocr_job_id": ocr_job_id,
                },
            )

            await connection.execute(
                text("DELETE FROM ai_job WHERE id = :id"),
                {"id": ai_job_id},
            )

            result = await connection.execute(
                text(
                    """
                    SELECT ai_job_id
                    FROM ocr_job
                    WHERE id = :id
                    """
                ),
                {"id": ocr_job_id},
            )

            assert result.scalar_one() is None
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_one_ai_job_cannot_map_to_multiple_ocr_jobs(
    migrated_engine: AsyncEngine,
) -> None:
    async with migrated_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            user_id, document_id, first_ocr_job_id = await insert_ocr_parent_chain(connection)
            second_ocr_job_id = str(uuid4())
            ai_job_id = await _insert_ai_job_for_ocr_mapping(
                connection,
                user_id=user_id,
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
                    "id": second_ocr_job_id,
                    "document_id": document_id,
                },
            )

            await connection.execute(
                text(
                    """
                    UPDATE ocr_job
                    SET ai_job_id = :ai_job_id
                    WHERE id = :ocr_job_id
                    """
                ),
                {
                    "ai_job_id": ai_job_id,
                    "ocr_job_id": first_ocr_job_id,
                },
            )

            with pytest.raises(
                IntegrityError,
                match="uq_ocr_job_ai_job",
            ):
                await connection.execute(
                    text(
                        """
                        UPDATE ocr_job
                        SET ai_job_id = :ai_job_id
                        WHERE id = :ocr_job_id
                        """
                    ),
                    {
                        "ai_job_id": ai_job_id,
                        "ocr_job_id": second_ocr_job_id,
                    },
                )
        finally:
            await transaction.rollback()


async def _fetch_ocr_ai_job_column_exists() -> bool:
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'ocr_job'
                          AND column_name = 'ai_job_id'
                    )
                    """
                )
            )
            return bool(result.scalar_one())
    finally:
        await engine.dispose()


async def _fetch_ocr_ai_job_id(ocr_job_id: str) -> str | None:
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT ai_job_id
                    FROM ocr_job
                    WHERE id = :id
                    """
                ),
                {"id": ocr_job_id},
            )
            return result.scalar_one()
    finally:
        await engine.dispose()


async def _insert_pre_mapping_ocr_job() -> tuple[str, str, str]:
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        async with engine.begin() as connection:
            return await insert_ocr_parent_chain(connection)
    finally:
        await engine.dispose()


async def _cleanup_ocr_mapping_roundtrip_data(
    *,
    user_id: str,
    document_id: str,
    ocr_job_id: str,
) -> None:
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM ocr_job WHERE id = :id"),
                {"id": ocr_job_id},
            )
            await connection.execute(
                text("DELETE FROM medical_document WHERE id = :id"),
                {"id": document_id},
            )
            await connection.execute(
                text("DELETE FROM profile WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            await connection.execute(
                text('DELETE FROM "user" WHERE id = :id'),
                {"id": user_id},
            )
    finally:
        await engine.dispose()


def test_ocr_ai_job_mapping_migration_roundtrips_and_preserves_existing_rows() -> None:
    alembic_config = create_alembic_config()
    user_id = ""
    document_id = ""
    ocr_job_id = ""

    try:
        command.downgrade(
            alembic_config,
            OCR_AI_JOB_BASE_REVISION,
        )
        assert asyncio.run(_fetch_ocr_ai_job_column_exists()) is False

        user_id, document_id, ocr_job_id = asyncio.run(_insert_pre_mapping_ocr_job())

        command.upgrade(alembic_config, "head")

        assert asyncio.run(_fetch_ocr_ai_job_column_exists()) is True
        assert asyncio.run(_fetch_ocr_ai_job_id(ocr_job_id)) is None

        # 이미 head인 상태에서 다시 실행해도 추가 변경 없이 성공해야 합니다.
        command.upgrade(alembic_config, "head")

        assert asyncio.run(_fetch_ocr_ai_job_id(ocr_job_id)) is None

        command.downgrade(
            alembic_config,
            OCR_AI_JOB_BASE_REVISION,
        )
        assert asyncio.run(_fetch_ocr_ai_job_column_exists()) is False

        command.upgrade(alembic_config, "head")

        assert asyncio.run(_fetch_ocr_ai_job_column_exists()) is True
        assert asyncio.run(_fetch_ocr_ai_job_id(ocr_job_id)) is None
    finally:
        command.upgrade(alembic_config, "head")

        if user_id and document_id and ocr_job_id:
            asyncio.run(
                _cleanup_ocr_mapping_roundtrip_data(
                    user_id=user_id,
                    document_id=document_id,
                    ocr_job_id=ocr_job_id,
                )
            )


async def _insert_linked_ocr_ai_job() -> tuple[str, str, str, str]:
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        async with engine.begin() as connection:
            user_id, document_id, ocr_job_id = await insert_ocr_parent_chain(connection)
            ai_job_id = await _insert_ai_job_for_ocr_mapping(
                connection,
                user_id=user_id,
            )

            await connection.execute(
                text(
                    """
                    UPDATE ocr_job
                    SET ai_job_id = :ai_job_id
                    WHERE id = :ocr_job_id
                    """
                ),
                {
                    "ai_job_id": ai_job_id,
                    "ocr_job_id": ocr_job_id,
                },
            )

            return user_id, document_id, ocr_job_id, ai_job_id
    finally:
        await engine.dispose()


async def _cleanup_linked_ocr_ai_job(
    *,
    user_id: str,
    document_id: str,
    ocr_job_id: str,
    ai_job_id: str,
) -> None:
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM ocr_job WHERE id = :id"),
                {"id": ocr_job_id},
            )
            await connection.execute(
                text("DELETE FROM ai_job WHERE id = :id"),
                {"id": ai_job_id},
            )
            await connection.execute(
                text("DELETE FROM medical_document WHERE id = :id"),
                {"id": document_id},
            )
            await connection.execute(
                text("DELETE FROM profile WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            await connection.execute(
                text('DELETE FROM "user" WHERE id = :id'),
                {"id": user_id},
            )
    finally:
        await engine.dispose()


async def _write_ocr_ai_job_link_and_wait(
    *,
    writer_ready: Event,
    release_writer: Event,
) -> tuple[str, str, str, str]:
    """OCR mapping을 기록한 transaction을 commit 직전까지 유지합니다."""
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()

            try:
                user_id, document_id, ocr_job_id = await insert_ocr_parent_chain(connection)
                ai_job_id = await _insert_ai_job_for_ocr_mapping(
                    connection,
                    user_id=user_id,
                )

                await connection.execute(
                    text(
                        """
                        UPDATE ocr_job
                        SET ai_job_id = :ai_job_id
                        WHERE id = :ocr_job_id
                        """
                    ),
                    {
                        "ai_job_id": ai_job_id,
                        "ocr_job_id": ocr_job_id,
                    },
                )

                writer_ready.set()

                released = await asyncio.to_thread(
                    release_writer.wait,
                    10,
                )
                if not released:
                    raise TimeoutError("Timed out waiting to release the concurrent OCR writer.")

                await transaction.commit()

                return user_id, document_id, ocr_job_id, ai_job_id
            except BaseException:
                if transaction.is_active:
                    await transaction.rollback()
                raise
    finally:
        await engine.dispose()


async def _wait_for_ocr_downgrade_lock() -> None:
    """downgrade가 ocr_job ACCESS EXCLUSIVE lock을 기다리는지 확인합니다."""
    engine = create_async_engine(
        create_alembic_database_url(),
        poolclass=NullPool,
    )

    try:
        for _ in range(100):
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_locks AS lock_info
                            JOIN pg_class AS table_info
                              ON table_info.oid = lock_info.relation
                            JOIN pg_namespace AS namespace_info
                              ON namespace_info.oid = table_info.relnamespace
                            WHERE namespace_info.nspname = 'public'
                              AND table_info.relname = 'ocr_job'
                              AND lock_info.mode = 'AccessExclusiveLock'
                              AND lock_info.granted = false
                        )
                        """
                    )
                )

                if result.scalar_one():
                    return

            await asyncio.sleep(0.05)

        raise AssertionError("Downgrade did not wait for the ocr_job ACCESS EXCLUSIVE lock.")
    finally:
        await engine.dispose()


def test_ocr_ai_job_mapping_downgrade_blocks_concurrent_link_write() -> None:
    alembic_config = create_alembic_config()
    writer_ready = Event()
    release_writer = Event()

    user_id = ""
    document_id = ""
    ocr_job_id = ""
    ai_job_id = ""

    writer_future: Future[tuple[str, str, str, str]] | None = None
    downgrade_future: Future[None] | None = None

    try:
        command.upgrade(alembic_config, "head")

        with ThreadPoolExecutor(max_workers=2) as executor:
            writer_future = executor.submit(
                asyncio.run,
                _write_ocr_ai_job_link_and_wait(
                    writer_ready=writer_ready,
                    release_writer=release_writer,
                ),
            )

            if not writer_ready.wait(timeout=10):
                if writer_future.done():
                    writer_future.result()

                raise AssertionError("Concurrent OCR writer did not reach the uncommitted state.")

            downgrade_future = executor.submit(
                command.downgrade,
                alembic_config,
                OCR_AI_JOB_BASE_REVISION,
            )

            asyncio.run(_wait_for_ocr_downgrade_lock())

            # writer가 commit하기 전에는 downgrade가 완료되면 안 됩니다.
            assert downgrade_future.done() is False

            release_writer.set()

            user_id, document_id, ocr_job_id, ai_job_id = writer_future.result(timeout=10)

            # lock 획득 후 다시 검사하므로 방금 commit된 연결을 확인하고
            # 컬럼 삭제를 거부해야 합니다.
            with pytest.raises(
                RuntimeError,
                match=r"Cannot downgrade while ocr_job\.ai_job_id contains linked AI Jobs",
            ):
                downgrade_future.result(timeout=10)

        assert asyncio.run(_fetch_ocr_ai_job_column_exists()) is True
        assert asyncio.run(_fetch_ocr_ai_job_id(ocr_job_id)) == ai_job_id
    finally:
        release_writer.set()

        if writer_future is not None and not writer_future.done():
            writer_future.result(timeout=10)

        if downgrade_future is not None and not downgrade_future.done():
            try:
                downgrade_future.result(timeout=10)
            except RuntimeError:
                pass

        command.upgrade(alembic_config, "head")

        if user_id and document_id and ocr_job_id and ai_job_id:
            asyncio.run(
                _cleanup_linked_ocr_ai_job(
                    user_id=user_id,
                    document_id=document_id,
                    ocr_job_id=ocr_job_id,
                    ai_job_id=ai_job_id,
                )
            )


def test_ocr_ai_job_mapping_downgrade_rejects_linked_data() -> None:
    alembic_config = create_alembic_config()
    user_id = ""
    document_id = ""
    ocr_job_id = ""
    ai_job_id = ""

    try:
        command.upgrade(alembic_config, "head")

        user_id, document_id, ocr_job_id, ai_job_id = asyncio.run(_insert_linked_ocr_ai_job())

        with pytest.raises(
            RuntimeError,
            match=r"Cannot downgrade while ocr_job\.ai_job_id contains linked AI Jobs",
        ):
            command.downgrade(
                alembic_config,
                OCR_AI_JOB_BASE_REVISION,
            )

        assert asyncio.run(_fetch_ocr_ai_job_column_exists()) is True
        assert asyncio.run(_fetch_ocr_ai_job_id(ocr_job_id)) == ai_job_id
    finally:
        command.upgrade(alembic_config, "head")

        if user_id and document_id and ocr_job_id and ai_job_id:
            asyncio.run(
                _cleanup_linked_ocr_ai_job(
                    user_id=user_id,
                    document_id=document_id,
                    ocr_job_id=ocr_job_id,
                    ai_job_id=ai_job_id,
                )
            )


@pytest.mark.asyncio
async def test_user_account_lifecycle_columns_default_to_active(
    migrated_engine: AsyncEngine,
) -> None:
    """PD-206: 신규 계정은 `account_status=ACTIVE`, `token_version=0`으로 시작하고,
    기존 계정도 이 migration으로 같은 기본값을 갖게 됩니다."""
    async with migrated_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            user_id = str(uuid4())
            await connection.execute(
                text(
                    """
                    INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin)
                    VALUES (:id, :email, 'hashed', '테스트 사용자', true, false)
                    """
                ),
                {"id": user_id, "email": f"u{uuid4().hex[:12]}@t.local"},
            )

            result = await connection.execute(
                text(
                    """
                    SELECT account_status, withdrawal_requested_at, withdrawn_at, token_version
                    FROM "user"
                    WHERE id = :id
                    """
                ),
                {"id": user_id},
            )
            row = result.one()
        finally:
            await transaction.rollback()

    assert row.account_status == "ACTIVE"
    assert row.withdrawal_requested_at is None
    assert row.withdrawn_at is None
    assert row.token_version == 0
