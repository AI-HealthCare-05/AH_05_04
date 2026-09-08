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
                    'prescription',
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
            await connection.execute(text("DELETE FROM prescription WHERE id = :prescription_id"), ids)
            await connection.execute(text("DELETE FROM ocr_job WHERE id = :ocr_job_id"), ids)
            await connection.execute(text("DELETE FROM medical_document WHERE id = :document_id"), ids)
            await connection.execute(text("DELETE FROM profile WHERE id = :profile_id"), ids)
            await connection.execute(text('DELETE FROM "user" WHERE id = :user_id'), ids)


async def _activate_version(ids: Mapping[str, str], version_id: str | None = None) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text("UPDATE prescription SET active_version_id = :active_version_id WHERE id = :prescription_id"),
                {**ids, "active_version_id": version_id or ids["version_id"]},
            )
            await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def _count_rows(table_name: str, where_clause: str, params: Mapping[str, object]) -> int:
    async with _connection() as connection:
        result = await connection.execute(
            text(f"SELECT count(*) FROM {table_name} WHERE {where_clause}"),
            params,
        )
        return int(result.scalar_one())


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
                await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
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
    assert "trg_prescription_version_register_assembly" in schema_objects
    assert "trg_prescription_version_medication_prevent_frozen_insert" in schema_objects
    assert "trg_prescription_version_medication_required" in schema_objects
    assert "trg_prescription_active_version_medication" in schema_objects


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

        async def reject_blank_medication_during_assembly() -> None:
            async with _connection() as connection:
                transaction = await connection.begin()
                try:
                    invalid_version_id = str(uuid4())
                    await connection.execute(
                        text(
                            """
                            INSERT INTO prescription_version (
                                id, prescription_id, version_number, prescribed_date, confirmed_at
                            )
                            VALUES (:invalid_version_id, :prescription_id, 2, DATE '2026-09-07', now())
                            """
                        ),
                        {**ids, "invalid_version_id": invalid_version_id},
                    )
                    with pytest.raises(DBAPIError) as exc_info:
                        await connection.execute(
                            text(
                                """
                                INSERT INTO prescription_version_medication (
                                    id, prescription_version_id, medication_name, display_order
                                )
                                VALUES (:invalid_id, :invalid_version_id, '   ', 1)
                                """
                            ),
                            {
                                **ids,
                                "invalid_id": str(uuid4()),
                                "invalid_version_id": invalid_version_id,
                            },
                        )
                    assert "chk_prescription_version_medication_name_nonblank" in str(exc_info.value)
                finally:
                    await transaction.rollback()

        asyncio.run(reject_blank_medication_during_assembly())
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


def test_active_version_requires_at_least_one_medication() -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_prescription_version())
    empty_version_id = str(uuid4())

    async def create_empty_version_and_reject_activation() -> None:
        async with _connection() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text(
                        """
                        INSERT INTO prescription_version (
                            id, prescription_id, version_number, prescribed_date, confirmed_at
                        )
                        VALUES (:empty_version_id, :prescription_id, 2, DATE '2026-09-08', now())
                        """
                    ),
                    {**ids, "empty_version_id": empty_version_id},
                )
                await connection.execute(
                    text("UPDATE prescription SET active_version_id = :empty_version_id WHERE id = :prescription_id"),
                    {**ids, "empty_version_id": empty_version_id},
                )
                with pytest.raises(DBAPIError) as exc_info:
                    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                assert "prescription version requires at least one medication" in str(exc_info.value)
            finally:
                await transaction.rollback()

    try:
        asyncio.run(create_empty_version_and_reject_activation())
    finally:
        asyncio.run(_cleanup_prescription_version(ids))


def test_active_and_historical_version_medication_sets_are_frozen() -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_prescription_version())
    second_version_id = str(uuid4())
    second_medication_id = str(uuid4())

    async def create_second_version() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        """
                        INSERT INTO prescription_version (
                            id, prescription_id, version_number, prescribed_date, confirmed_at
                        )
                        VALUES (:second_version_id, :prescription_id, 2, DATE '2026-09-08', now())
                        """
                    ),
                    {**ids, "second_version_id": second_version_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO prescription_version_medication (
                            id, prescription_version_id, medication_name, display_order
                        )
                        VALUES (:second_medication_id, :second_version_id, '합성두번째약', 1)
                        """
                    ),
                    {
                        **ids,
                        "second_version_id": second_version_id,
                        "second_medication_id": second_medication_id,
                    },
                )
                await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    try:
        asyncio.run(_activate_version(ids))
        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO prescription_version_medication (
                    id, prescription_version_id, medication_name, display_order
                )
                VALUES (:late_id, :version_id, '활성버전추가약', 2)
                """,
                {**ids, "late_id": str(uuid4())},
                expected_text="prescription version medication set is frozen",
            )
        )

        asyncio.run(create_second_version())

        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO prescription_version_medication (
                    id, prescription_version_id, medication_name, display_order
                )
                VALUES (:late_id, :second_version_id, '커밋후추가약', 2)
                """,
                {
                    **ids,
                    "second_version_id": second_version_id,
                    "late_id": str(uuid4()),
                },
                expected_text="prescription version medication set is frozen",
            )
        )

        asyncio.run(_activate_version(ids, second_version_id))

        for frozen_version_id in (ids["version_id"], second_version_id):
            asyncio.run(
                _execute_expect_db_error(
                    """
                    INSERT INTO prescription_version_medication (
                        id, prescription_version_id, medication_name, display_order
                    )
                    VALUES (:late_id, :frozen_version_id, '과거버전추가약', 2)
                    """,
                    {
                        **ids,
                        "frozen_version_id": frozen_version_id,
                        "late_id": str(uuid4()),
                    },
                    expected_text="prescription version medication set is frozen",
                )
            )
    finally:
        asyncio.run(_cleanup_prescription_version(ids))


def test_deferred_active_fk_supports_future_not_null_creation_transaction() -> None:
    command.upgrade(create_alembic_config(), "head")

    async def create_graph_with_final_not_null_shape() -> None:
        ids = {
            "user_id": str(uuid4()),
            "profile_id": str(uuid4()),
            "document_id": str(uuid4()),
            "ocr_job_id": str(uuid4()),
            "prescription_id": str(uuid4()),
            "version_id": str(uuid4()),
            "medication_id": str(uuid4()),
            "email": f"deferred-{uuid4().hex[:10]}@test.local",
        }
        async with _connection() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("ALTER TABLE prescription ALTER COLUMN active_version_id SET NOT NULL"))
                await connection.execute(
                    text(
                        """
                        INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin)
                        VALUES (:user_id, :email, 'synthetic-password-hash', 'deferred-test', true, false)
                        """
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO profile (id, user_id, profile_type, display_name)
                        VALUES (:profile_id, :user_id, 'SELF', 'deferred-test')
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
                            'deferred-test.png', 'synthetic/deferred-test.png',
                            'image/png', 1, 'UPLOADED'
                        )
                        """
                    ),
                    ids,
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
                # Prescription points to a Version that does not exist yet. This
                # succeeds only because the active FK is initially deferred.
                nested_transaction = await connection.begin_nested()
                try:
                    # The registration trigger runs inside this SAVEPOINT. Its
                    # transaction-local assembly ID must survive RELEASE.
                    await connection.execute(
                        text(
                            """
                            INSERT INTO prescription (
                                id, active_version_id, document_id, source_ocr_job_id,
                                profile_id, prescribed_date, prescription_status, confirmed_at
                            )
                            VALUES (
                                :prescription_id, :version_id, :document_id, :ocr_job_id,
                                :profile_id, DATE '2026-09-08', 'CONFIRMED', now()
                            )
                            """
                        ),
                        ids,
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO prescription_version (
                                id, prescription_id, version_number, prescribed_date, confirmed_at
                            )
                            VALUES (:version_id, :prescription_id, 1, DATE '2026-09-08', now())
                            """
                        ),
                        ids,
                    )
                    await nested_transaction.commit()
                except BaseException:
                    await nested_transaction.rollback()
                    raise
                # Medication assembly continues after SAVEPOINT release.
                await connection.execute(
                    text(
                        """
                        INSERT INTO prescription_version_medication (
                            id, prescription_version_id, medication_name, display_order
                        )
                        VALUES (:medication_id, :version_id, '합성지연제약약', 1)
                        """
                    ),
                    ids,
                )
                await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            finally:
                await transaction.rollback()

    asyncio.run(create_graph_with_final_not_null_shape())


def test_create_then_parent_delete_same_transaction_commits() -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_prescription_version())
    replacement_version_id = str(uuid4())
    replacement_medication_id = str(uuid4())

    async def replace_and_delete_graph() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(text("DELETE FROM prescription WHERE id = :prescription_id"), ids)
                await connection.execute(
                    text(
                        """
                        INSERT INTO prescription (
                            id, active_version_id, document_id, source_ocr_job_id,
                            profile_id, prescribed_date, prescription_status, confirmed_at
                        )
                        VALUES (
                            :prescription_id, :replacement_version_id, :document_id, :ocr_job_id,
                            :profile_id, DATE '2026-09-08', 'CONFIRMED', now()
                        )
                        """
                    ),
                    {**ids, "replacement_version_id": replacement_version_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO prescription_version (
                            id, prescription_id, version_number, prescribed_date, confirmed_at
                        )
                        VALUES (:replacement_version_id, :prescription_id, 1, DATE '2026-09-08', now())
                        """
                    ),
                    {**ids, "replacement_version_id": replacement_version_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO prescription_version_medication (
                            id, prescription_version_id, medication_name, display_order
                        )
                        VALUES (:replacement_medication_id, :replacement_version_id, '합성교체약', 1)
                        """
                    ),
                    {
                        **ids,
                        "replacement_version_id": replacement_version_id,
                        "replacement_medication_id": replacement_medication_id,
                    },
                )
                await connection.execute(text("DELETE FROM prescription WHERE id = :prescription_id"), ids)

    try:
        asyncio.run(replace_and_delete_graph())
        assert asyncio.run(_count_rows("prescription", "id = :prescription_id", ids)) == 0
        assert asyncio.run(_count_rows("prescription_version", "prescription_id = :prescription_id", ids)) == 0
    finally:
        asyncio.run(_cleanup_prescription_version(ids))


def test_update_then_parent_delete_same_transaction_commits() -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_prescription_version())

    async def activate_and_delete_parent() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("UPDATE prescription SET active_version_id = :version_id WHERE id = :prescription_id"),
                    ids,
                )
                await connection.execute(text("DELETE FROM prescription WHERE id = :prescription_id"), ids)

    try:
        asyncio.run(activate_and_delete_parent())
        assert asyncio.run(_count_rows("prescription", "id = :prescription_id", ids)) == 0
        assert asyncio.run(_count_rows("prescription_version", "prescription_id = :prescription_id", ids)) == 0
    finally:
        asyncio.run(_cleanup_prescription_version(ids))


def test_parent_prescription_delete_cascades_immutable_snapshots() -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_prescription_version())
    try:
        asyncio.run(_activate_version(ids))

        async def delete_parent() -> None:
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(
                        text("DELETE FROM prescription WHERE id = :prescription_id"),
                        ids,
                    )

        asyncio.run(delete_parent())
        assert asyncio.run(_count_rows("prescription_version", "prescription_id = :prescription_id", ids)) == 0
        assert (
            asyncio.run(
                _count_rows(
                    "prescription_version_medication",
                    "id = :version_medication_id",
                    ids,
                )
            )
            == 0
        )
    finally:
        asyncio.run(_cleanup_prescription_version(ids))


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
