"""Track B Schedule·Occurrence PostgreSQL migration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone
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
SCHEDULE_REVISION = "199a1b2c3d4e"
SCHEDULE_BASE_REVISION = "169d4e5f6a7b"
SCHEDULE_TABLES = {
    "medication_schedule",
    "medication_schedule_time",
    "medication_occurrence",
}


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


async def _fetch_table_names() -> set[str]:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(:table_names)
                """
            ),
            {"table_names": list(SCHEDULE_TABLES)},
        )
        return {row[0] for row in result}


async def _fetch_schema_object_names() -> set[str]:
    async with _connection() as connection:
        constraints = await connection.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = ANY(:table_names)
                """
            ),
            {"table_names": list(SCHEDULE_TABLES)},
        )
        indexes = await connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = ANY(:table_names)
                """
            ),
            {"table_names": list(SCHEDULE_TABLES)},
        )
        return {*(row[0] for row in constraints), *(row[0] for row in indexes)}


async def _seed_graph() -> dict[str, str]:
    ids = {
        "user_id": str(uuid4()),
        "profile_id": str(uuid4()),
        "document_id": str(uuid4()),
        "ocr_job_id": str(uuid4()),
        "prescription_id": str(uuid4()),
        "version_id": str(uuid4()),
        "version_medication_id": str(uuid4()),
        "schedule_id": str(uuid4()),
        "schedule_time_id": str(uuid4()),
        "occurrence_id": str(uuid4()),
    }
    # Historical migration tests may seed before the fingerprint expansion.
    async with _connection() as probe:
        has_fingerprint = bool(
            await probe.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='prescription_version' AND column_name='content_hash'"
                )
            )
        )
    version_columns = ", medication_count, content_hash" if has_fingerprint else ""
    version_values = ", 1, :content_hash" if has_fingerprint else ""
    medication_columns = ", medication_count" if has_fingerprint else ""
    medication_values = ", 1" if has_fingerprint else ""
    from provider_contracts.prescription_integrity import prescription_fingerprint

    ids["content_hash"] = prescription_fingerprint(
        date(2026, 9, 9), [{"medication_name": "합성테스트약", "frequency_per_day": 1, "display_order": 1}]
    ).content_hash
    kst = timezone(timedelta(hours=9))
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin)
                    VALUES (:user_id, :email, 'synthetic-password-hash', 'schedule-test', true, false)
                    """
                ),
                {**ids, "email": f"schedule-{uuid4().hex[:12]}@test.local"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO profile (id, user_id, profile_type, display_name)
                    VALUES (:profile_id, :user_id, 'SELF', 'schedule-test')
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
                        'synthetic-schedule.png', :object_key, 'image/png', 1, 'UPLOADED'
                    )
                    """
                ),
                {**ids, "object_key": f"synthetic/{ids['document_id']}.png"},
            )
            await connection.execute(
                text("INSERT INTO ocr_job (id, document_id, ocr_status) VALUES (:ocr_job_id, :document_id, 'PENDING')"),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription (
                        id, active_version_id, document_id, source_ocr_job_id,
                        profile_id, prescribed_date, prescription_status, confirmed_at
                    )
                    VALUES (
                        :prescription_id, :version_id, :document_id, :ocr_job_id,
                        :profile_id, DATE '2026-09-09', 'CONFIRMED', :confirmed_at
                    )
                    """
                ),
                {**ids, "confirmed_at": datetime(2026, 9, 9, tzinfo=UTC)},
            )
            await connection.execute(
                text(
                    f"""
                    INSERT INTO prescription_version (
                        id, prescription_id, version_number, prescribed_date, confirmed_at{version_columns}
                    )
                    VALUES (:version_id, :prescription_id, 1, DATE '2026-09-09', :confirmed_at{version_values})
                    """
                ),
                {**ids, "confirmed_at": datetime(2026, 9, 9, tzinfo=UTC)},
            )
            await connection.execute(
                text(
                    f"""
                    INSERT INTO prescription_version_medication (
                        id, prescription_version_id, medication_name, frequency_per_day, display_order{medication_columns}
                    )
                    VALUES (:version_medication_id, :version_id, '합성테스트약', 1, 1{medication_values})
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medication_schedule (
                        id, prescription_version_medication_id, start_local_date,
                        end_mode, end_local_date, source, status, revision
                    )
                    VALUES (
                        :schedule_id, :version_medication_id, DATE '2026-09-09',
                        'OPEN_ENDED', NULL, 'USER_CONFIRMED', 'ACTIVE', 1
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medication_schedule_time (
                        id, medication_schedule_id, schedule_revision, local_time
                    )
                    VALUES (:schedule_time_id, :schedule_id, 1, TIME '09:00')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medication_occurrence (
                        id, medication_schedule_id, medication_schedule_time_id,
                        schedule_revision, scheduled_local_date, scheduled_at,
                        confirmation_deadline_at, status
                    )
                    VALUES (
                        :occurrence_id, :schedule_id, :schedule_time_id,
                        1, DATE '2026-09-09', :scheduled_at, :deadline_at, 'PENDING'
                    )
                    """
                ),
                {
                    **ids,
                    "scheduled_at": datetime(2026, 9, 9, 9, 0, tzinfo=kst),
                    "deadline_at": datetime(2026, 9, 10, 0, 0, tzinfo=kst),
                },
            )
    return ids


async def _cleanup_graph(ids: Mapping[str, str]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM medication_occurrence WHERE id = :occurrence_id"), ids)
            await connection.execute(text("DELETE FROM medication_schedule_time WHERE id = :schedule_time_id"), ids)
            await connection.execute(text("DELETE FROM medication_schedule WHERE id = :schedule_id"), ids)
            await connection.execute(text("DELETE FROM prescription WHERE id = :prescription_id"), ids)
            await connection.execute(text("DELETE FROM ocr_job WHERE id = :ocr_job_id"), ids)
            await connection.execute(text("DELETE FROM medical_document WHERE id = :document_id"), ids)
            await connection.execute(text("DELETE FROM profile WHERE id = :profile_id"), ids)
            await connection.execute(text('DELETE FROM "user" WHERE id = :user_id'), ids)


async def _execute_expect_constraint(sql: str, values: Mapping[str, object], *, constraint: str) -> None:
    async with _connection() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as exc_info:
                await connection.execute(text(sql), values)
            assert constraint in str(exc_info.value)
        finally:
            await transaction.rollback()


def test_schedule_migration_upgrade_and_downgrade() -> None:
    alembic_config = create_alembic_config()
    command.upgrade(alembic_config, "398b2c3d4e5f")
    try:
        command.downgrade(alembic_config, SCHEDULE_BASE_REVISION)
        assert asyncio.run(_fetch_table_names()) == set()
        command.upgrade(alembic_config, SCHEDULE_REVISION)
        assert asyncio.run(_fetch_table_names()) == SCHEDULE_TABLES
        schema_objects = asyncio.run(_fetch_schema_object_names())
        assert {
            "fk_medication_schedule_version_medication",
            "fk_medication_schedule_time_schedule",
            "fk_medication_occurrence_schedule",
            "fk_medication_occurrence_schedule_time",
            "uq_medication_schedule_version_medication",
            "uq_medication_schedule_time_revision_local_time",
            "uq_medication_occurrence_time_local_date",
            "idx_medication_occurrence_deadline_status",
        } <= schema_objects
    finally:
        command.upgrade(alembic_config, "398b2c3d4e5f")


def test_schedule_constraints_and_utc_storage() -> None:
    command.upgrade(create_alembic_config(), "398b2c3d4e5f")
    ids = asyncio.run(_seed_graph())
    try:
        asyncio.run(
            _execute_expect_constraint(
                """
                INSERT INTO medication_schedule (
                    id, prescription_version_medication_id, start_local_date,
                    end_mode, source, status, revision
                ) VALUES (
                    :duplicate_id, :version_medication_id, DATE '2026-09-09',
                    'OPEN_ENDED', 'USER_CONFIRMED', 'ACTIVE', 1
                )
                """,
                {**ids, "duplicate_id": str(uuid4())},
                constraint="uq_medication_schedule_version_medication",
            )
        )
        asyncio.run(
            _execute_expect_constraint(
                """
                INSERT INTO medication_schedule (
                    id, prescription_version_medication_id, start_local_date,
                    end_mode, source, status, revision
                ) VALUES (
                    :invalid_fk_id, :missing_medication_id, DATE '2026-09-09',
                    'OPEN_ENDED', 'USER_CONFIRMED', 'ACTIVE', 1
                )
                """,
                {
                    "invalid_fk_id": str(uuid4()),
                    "missing_medication_id": str(uuid4()),
                },
                constraint="fk_medication_schedule_version_medication",
            )
        )
        asyncio.run(
            _execute_expect_constraint(
                """
                INSERT INTO medication_schedule_time (
                    id, medication_schedule_id, schedule_revision, local_time
                ) VALUES (:duplicate_id, :schedule_id, 1, TIME '09:00')
                """,
                {**ids, "duplicate_id": str(uuid4())},
                constraint="uq_medication_schedule_time_revision_local_time",
            )
        )
        asyncio.run(
            _execute_expect_constraint(
                """
                INSERT INTO medication_occurrence (
                    id, medication_schedule_id, medication_schedule_time_id,
                    schedule_revision, scheduled_local_date, scheduled_at,
                    confirmation_deadline_at, status
                ) VALUES (
                    :duplicate_id, :schedule_id, :schedule_time_id,
                    1, DATE '2026-09-09', :scheduled_at, :deadline_at, 'PENDING'
                )
                """,
                {
                    **ids,
                    "duplicate_id": str(uuid4()),
                    "scheduled_at": datetime(2026, 9, 9, tzinfo=UTC),
                    "deadline_at": datetime(2026, 9, 9, 4, tzinfo=UTC),
                },
                constraint="uq_medication_occurrence_time_local_date",
            )
        )

        async def fetch_instants() -> tuple[datetime, datetime]:
            async with _connection() as connection:
                result = await connection.execute(
                    text(
                        """
                        SELECT scheduled_at, confirmation_deadline_at
                        FROM medication_occurrence
                        WHERE id = :occurrence_id
                        """
                    ),
                    ids,
                )
                row = result.one()
                return row.scheduled_at, row.confirmation_deadline_at

        scheduled_at, deadline_at = asyncio.run(fetch_instants())
        assert scheduled_at == datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
        assert deadline_at == datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    finally:
        asyncio.run(_cleanup_graph(ids))


def test_schedule_downgrade_rejects_existing_history() -> None:
    alembic_config = create_alembic_config()
    command.upgrade(alembic_config, "398b2c3d4e5f")
    ids = asyncio.run(_seed_graph())
    try:
        with pytest.raises(RuntimeError, match=f"Cannot downgrade revision {SCHEDULE_REVISION}"):
            command.downgrade(alembic_config, SCHEDULE_BASE_REVISION)
        assert asyncio.run(_fetch_table_names()) == SCHEDULE_TABLES
    finally:
        asyncio.run(_cleanup_graph(ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")
