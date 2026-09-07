"""Prescription Version foundation Alembic schema and rollback tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESCRIPTION_VERSION_REVISION = "169a1b2c3d4e"
PRESCRIPTION_VERSION_BASE_REVISION = "164f3a2b1c0d"


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


async def _table_exists(table_name: str) -> bool:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return bool(result.scalar_one())


async def _fetch_schema_object_names() -> set[str]:
    async with _connection() as connection:
        constraints = await connection.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'prescription',
                    'prescription_version',
                    'prescription_version_medication'
                  )
                """
            )
        )
        indexes = await connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename IN (
                    'prescription',
                    'prescription_version',
                    'prescription_version_medication'
                  )
                """
            )
        )
        triggers = await connection.execute(
            text(
                """
                SELECT trigger_name
                FROM information_schema.triggers
                WHERE event_object_schema = 'public'
                  AND event_object_table IN (
                    'prescription_version',
                    'prescription_version_medication'
                  )
                """
            )
        )
        return {
            *(row[0] for row in constraints),
            *(row[0] for row in indexes),
            *(row[0] for row in triggers),
        }


async def _seed_prescription_version() -> dict[str, str]:
    ids = {
        "user_id": str(uuid4()),
        "profile_id": str(uuid4()),
        "document_id": str(uuid4()),
        "ocr_job_id": str(uuid4()),
        "prescription_id": str(uuid4()),
        "version_id": str(uuid4()),
        "version_medication_id": str(uuid4()),
    }
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO "user" (
                        id, email, hashed_password, name, is_active, is_admin
                    )
                    VALUES (
                        :user_id, :email, 'synthetic-password-hash',
                        'version-test', true, false
                    )
                    """
                ),
                {**ids, "email": f"version-{uuid4().hex[:12]}@test.local"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO profile (id, user_id, profile_type, display_name)
                    VALUES (:profile_id, :user_id, 'SELF', 'version-test')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medical_document (
                        id, uploaded_by, profile_id, document_type, original_file_name,
                        object_key, file_mime_type, file_size_bytes, upload_status
                    )
                    VALUES (
                        :document_id, :user_id, :profile_id, 'PRESCRIPTION',
                        'synthetic-version-test.png', :object_key, 'image/png', 1, 'UPLOADED'
                    )
                    """
                ),
                {**ids, "object_key": f"synthetic/{ids['document_id']}.png"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ocr_job (id, document_id, ocr_status)
                    VALUES (:ocr_job_id, :document_id, 'PENDING')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription (
                        id, document_id, source_ocr_job_id, profile_id, prescribed_date,
                        prescription_status, confirmed_at
                    )
                    VALUES (
                        :prescription_id, :document_id, :ocr_job_id, :profile_id,
                        :prescribed_date, 'CONFIRMED', :confirmed_at
                    )
                    """
                ),
                {
                    **ids,
                    "prescribed_date": date(2026, 9, 7),
                    "confirmed_at": datetime.now(UTC),
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription_version (
                        id, prescription_id, version_number, prescribed_date, confirmed_at
                    )
                    VALUES (
                        :version_id, :prescription_id, 1, :prescribed_date, :confirmed_at
                    )
                    """
                ),
                {
                    **ids,
                    "prescribed_date": date(2026, 9, 7),
                    "confirmed_at": datetime.now(UTC),
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription_version_medication (
                        id, prescription_version_id, medication_name, display_order
                    )
                    VALUES (
                        :version_medication_id, :version_id, '합성테스트약', 1
                    )
                    """
                ),
                ids,
            )
    return ids


async def _cleanup_prescription_version(ids: Mapping[str, str]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text("UPDATE prescription SET active_version_id = NULL WHERE id = :prescription_id"),
                ids,
            )
            for table_name in ("prescription_version_medication", "prescription_version"):
                await connection.execute(text(f"ALTER TABLE {table_name} DISABLE TRIGGER USER"))
            await connection.execute(
                text("DELETE FROM prescription_version_medication WHERE prescription_version_id = :version_id"),
                ids,
            )
            await connection.execute(
                text("DELETE FROM prescription_version WHERE id = :version_id"),
                ids,
            )
            for table_name in ("prescription_version_medication", "prescription_version"):
                await connection.execute(text(f"ALTER TABLE {table_name} ENABLE TRIGGER USER"))
            await connection.execute(text("DELETE FROM prescription WHERE id = :prescription_id"), ids)
            await connection.execute(text("DELETE FROM ocr_job WHERE id = :ocr_job_id"), ids)
            await connection.execute(text("DELETE FROM medical_document WHERE id = :document_id"), ids)
            await connection.execute(text("DELETE FROM profile WHERE id = :profile_id"), ids)
            await connection.execute(text('DELETE FROM "user" WHERE id = :user_id'), ids)


async def _execute_expect_db_error(
    sql: str,
    params: Mapping[str, object],
    *,
    expected_text: str,
) -> None:
    async with _connection() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as exc_info:
                await connection.execute(text(sql), params)
            assert expected_text in str(exc_info.value)
        finally:
            await transaction.rollback()


def test_prescription_version_empty_downgrade_roundtrips() -> None:
    alembic_config = create_alembic_config()
    try:
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, PRESCRIPTION_VERSION_BASE_REVISION)
        assert asyncio.run(_table_exists("prescription_version")) is False
        assert asyncio.run(_table_exists("prescription_version_medication")) is False
        command.upgrade(alembic_config, "head")
        assert asyncio.run(_table_exists("prescription_version")) is True
    finally:
        command.upgrade(alembic_config, "head")


def test_prescription_version_schema_constraints_exist() -> None:
    command.upgrade(create_alembic_config(), "head")
    schema_objects = asyncio.run(_fetch_schema_object_names())

    assert "fk_prescription_active_version" in schema_objects
    assert "uq_prescription_version_number" in schema_objects
    assert "chk_prescription_version_number" in schema_objects
    assert "uq_prescription_version_medication_order" in schema_objects
    assert "chk_prescription_version_medication_name_nonblank" in schema_objects
    assert "idx_prescription_active_version" in schema_objects
    assert "trg_prescription_version_prevent_update" in schema_objects
    assert "trg_prescription_version_prevent_delete" in schema_objects
    assert "trg_prescription_version_medication_prevent_update" in schema_objects
    assert "trg_prescription_version_medication_prevent_delete" in schema_objects


def test_prescription_version_constraints_and_immutability_are_enforced() -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_prescription_version())
    try:
        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO prescription_version (
                    id, prescription_id, version_number, prescribed_date, confirmed_at
                )
                VALUES (:duplicate_id, :prescription_id, 1, DATE '2026-09-07', now())
                """,
                {**ids, "duplicate_id": str(uuid4())},
                expected_text="uq_prescription_version_number",
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO prescription_version_medication (
                    id, prescription_version_id, medication_name, display_order
                )
                VALUES (:invalid_id, :version_id, '   ', 2)
                """,
                {**ids, "invalid_id": str(uuid4())},
                expected_text="chk_prescription_version_medication_name_nonblank",
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                """
                UPDATE prescription_version
                SET prescribed_date = DATE '2026-09-08'
                WHERE id = :version_id
                """,
                ids,
                expected_text="rows are immutable",
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                "DELETE FROM prescription_version_medication WHERE id = :version_medication_id",
                ids,
                expected_text="rows are immutable",
            )
        )
    finally:
        asyncio.run(_cleanup_prescription_version(ids))


def test_active_version_must_belong_to_the_same_prescription() -> None:
    command.upgrade(create_alembic_config(), "head")
    first = asyncio.run(_seed_prescription_version())
    second = asyncio.run(_seed_prescription_version())
    try:
        asyncio.run(
            _execute_expect_db_error(
                """
                UPDATE prescription
                SET active_version_id = :other_version_id
                WHERE id = :prescription_id
                """,
                {
                    "prescription_id": first["prescription_id"],
                    "other_version_id": second["version_id"],
                },
                expected_text="fk_prescription_active_version",
            )
        )
    finally:
        asyncio.run(_cleanup_prescription_version(first))
        asyncio.run(_cleanup_prescription_version(second))


def test_prescription_version_downgrade_rejects_immutable_history() -> None:
    alembic_config = create_alembic_config()
    command.upgrade(alembic_config, "head")
    ids = asyncio.run(_seed_prescription_version())
    try:
        with pytest.raises(RuntimeError, match=f"Cannot downgrade revision {PRESCRIPTION_VERSION_REVISION}"):
            command.downgrade(alembic_config, PRESCRIPTION_VERSION_BASE_REVISION)
        assert asyncio.run(_table_exists("prescription_version")) is True
    finally:
        command.upgrade(alembic_config, "head")
        asyncio.run(_cleanup_prescription_version(ids))
