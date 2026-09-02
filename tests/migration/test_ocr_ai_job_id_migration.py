import asyncio
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from test_postgresql_schema import create_alembic_config, create_test_database_url

AI_JOB_MIGRATION_REVISION = "146a1b2c3d4e"
OCR_AI_JOB_ID_REVISION = "158e9f2a4b7c"


async def _fetch_alembic_state() -> tuple[list[str], str]:
    engine = create_async_engine(create_test_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            versions = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
            is_nullable = (
                await connection.execute(
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
            ).scalar_one()
            return list(versions), str(is_nullable)
    finally:
        await engine.dispose()


def test_upgrading_to_head_twice_is_a_safe_no_op() -> None:
    """이미 head에 있는 상태에서 upgrade head를 다시 실행해도 에러나 중복 적용이 없는지 확인합니다."""
    alembic_config = create_alembic_config()

    command.upgrade(alembic_config, "head")
    command.upgrade(alembic_config, "head")

    versions, is_nullable = asyncio.run(_fetch_alembic_state())

    assert versions == [OCR_AI_JOB_ID_REVISION]
    assert is_nullable == "YES"


async def _insert_ocr_job_with_ai_job_id(ai_job_id: str) -> tuple[str, str, str]:
    engine = create_async_engine(create_test_database_url(), poolclass=NullPool)
    user_id = str(uuid4())
    profile_id = str(uuid4())
    document_id = str(uuid4())

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin)
                    VALUES (:id, :email, 'migration-test-password-hash', 'ai-job-fk-test', true, false)
                    """
                ),
                {"id": user_id, "email": f"ai-job-fk-{uuid4().hex[:12]}@test.local"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO profile (id, user_id, profile_type, display_name)
                    VALUES (:id, :user_id, 'SELF', 'ai-job-fk-test')
                    """
                ),
                {"id": profile_id, "user_id": user_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medical_document (
                        id, uploaded_by, profile_id, document_type,
                        original_file_name, object_key, file_mime_type, file_size_bytes, upload_status
                    )
                    VALUES (
                        :id, :uploaded_by, :profile_id, 'PRESCRIPTION',
                        'ai-job-fk-test.png', :object_key, 'image/png', 1, 'UPLOADED'
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
                    INSERT INTO ocr_job (id, document_id, ocr_status, ai_job_id)
                    VALUES (:id, :document_id, 'PENDING', :ai_job_id)
                    """
                ),
                {"id": str(uuid4()), "document_id": document_id, "ai_job_id": ai_job_id},
            )
    finally:
        await engine.dispose()

    return user_id, profile_id, document_id


async def _cleanup_ocr_job_fk_test_data(*, user_id: str, profile_id: str, document_id: str) -> None:
    engine = create_async_engine(create_test_database_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM medical_document WHERE id = :id"), {"id": document_id})
            await connection.execute(text("DELETE FROM profile WHERE id = :id"), {"id": profile_id})
            await connection.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user_id})
    finally:
        await engine.dispose()


def test_ocr_job_ai_job_id_fk_rejects_unknown_ai_job() -> None:
    """존재하지 않는 ai_job.id를 참조하면 FK 위반으로 거부되는지 확인합니다."""
    missing_ai_job_id = str(uuid4())

    with pytest.raises(IntegrityError, match="fk_ocr_job_ai_job"):
        asyncio.run(_insert_ocr_job_with_ai_job_id(missing_ai_job_id))


async def _fetch_ocr_job_has_ai_job_id_column() -> bool:
    engine = create_async_engine(create_test_database_url(), poolclass=NullPool)
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


def test_ocr_ai_job_id_downgrade_removes_column_and_upgrade_restores_it() -> None:
    """이 migration의 downgrade/upgrade가 컬럼을 안전하게 추가·제거하는지 왕복 검증합니다."""
    alembic_config = create_alembic_config()

    try:
        command.downgrade(alembic_config, AI_JOB_MIGRATION_REVISION)
        assert asyncio.run(_fetch_ocr_job_has_ai_job_id_column()) is False

        command.upgrade(alembic_config, "head")
        assert asyncio.run(_fetch_ocr_job_has_ai_job_id_column()) is True
    finally:
        command.upgrade(alembic_config, "head")
