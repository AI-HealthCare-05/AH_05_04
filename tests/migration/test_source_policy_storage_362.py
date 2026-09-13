"""Verify Source migration roundtrips and loss guards on isolated synthetic rows."""

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

_PATH = Path(__file__).resolve().parents[2] / "backend/alembic/versions/362a1b2c3d4e_source_policy_storage.py"
_SPEC = importlib.util.spec_from_file_location("source_policy_storage_362", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)

_ATTEMPT_PATH = _PATH.with_name("362b2c3d4e5f_source_attempt_provenance.py")
_ATTEMPT_SPEC = importlib.util.spec_from_file_location("source_attempt_provenance_362", _ATTEMPT_PATH)
assert _ATTEMPT_SPEC is not None and _ATTEMPT_SPEC.loader is not None
_ATTEMPT_MIGRATION = importlib.util.module_from_spec(_ATTEMPT_SPEC)
_ATTEMPT_SPEC.loader.exec_module(_ATTEMPT_MIGRATION)


def _upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        _MIGRATION.upgrade()


def _downgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        _MIGRATION.downgrade()


def _attempt_upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        _ATTEMPT_MIGRATION.upgrade()


def _attempt_downgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        _ATTEMPT_MIGRATION.downgrade()


@pytest.mark.asyncio
@pytest.mark.parametrize("version_length", [200, 201])
async def test_forward_migration_preserves_versions_or_refuses_without_truncation(version_length: int):
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("CREATE SCHEMA source_policy_362_migration_test"))
                await connection.execute(text("SET LOCAL search_path TO source_policy_362_migration_test"))
                await connection.execute(text("CREATE TABLE rag_source (id integer PRIMARY KEY)"))
                await connection.execute(
                    text(
                        "CREATE TABLE rag_source_snapshot (id integer PRIMARY KEY, source_version varchar(255) NOT NULL, UNIQUE(id, source_version))"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE rag_citation (source_snapshot_id integer, source_version varchar(255) NOT NULL, CONSTRAINT fk_rag_citation_snapshot_version FOREIGN KEY (source_snapshot_id, source_version) REFERENCES rag_source_snapshot(id, source_version))"
                    )
                )
                await connection.execute(text("INSERT INTO rag_source VALUES (1)"))
                await connection.execute(
                    text("INSERT INTO rag_source_snapshot VALUES (1, :version)"), {"version": "v" * version_length}
                )
                await connection.execute(
                    text("INSERT INTO rag_citation VALUES (1, :version)"), {"version": "v" * version_length}
                )
                if version_length > 200:
                    with pytest.raises(RuntimeError, match="exceeds PD-362"):
                        await connection.run_sync(_upgrade)
                    assert (
                        await connection.scalar(text("SELECT length(source_version) FROM rag_source_snapshot")) == 201
                    )
                else:
                    await connection.run_sync(_upgrade)
                    row = (
                        await connection.execute(
                            text("SELECT max_rejected_records, max_rejection_rate, empty_result_policy FROM rag_source")
                        )
                    ).one()
                    assert tuple(row) == (0, 0, "REJECT")
                    assert await connection.scalar(text("SELECT length(source_version) FROM rag_citation")) == 200
                    assert await connection.scalar(text("SELECT external_version FROM rag_source_snapshot")) is None
                    await connection.run_sync(_downgrade)
                    assert await connection.scalar(text("SELECT source_version FROM rag_citation")) == "v" * 200
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT character_maximum_length FROM information_schema.columns "
                                "WHERE table_schema = current_schema() AND table_name = 'rag_source_snapshot' "
                                "AND column_name = 'source_version'"
                            )
                        )
                        == 255
                    )
                    async with connection.begin_nested() as savepoint:
                        with pytest.raises(IntegrityError):
                            await connection.execute(text("INSERT INTO rag_citation VALUES (999, 'missing')"))
                        await savepoint.rollback()
                    await connection.run_sync(_upgrade)
                    assert await connection.scalar(text("SELECT length(source_version) FROM rag_citation")) == 200
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@asynccontextmanager
async def _isolated_schema():
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            async with connection.begin() as transaction:
                await connection.execute(text("CREATE SCHEMA source_362_downgrade_test"))
                await connection.execute(text("SET LOCAL search_path TO source_362_downgrade_test"))
                await connection.execute(text("CREATE TABLE rag_source (id integer PRIMARY KEY)"))
                await connection.execute(
                    text(
                        "CREATE TABLE rag_source_snapshot (id integer PRIMARY KEY, source_version varchar(255) "
                        "NOT NULL, UNIQUE(id, source_version))"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE rag_citation (source_snapshot_id integer, source_version varchar(255), "
                        "CONSTRAINT fk_rag_citation_snapshot_version FOREIGN KEY (source_snapshot_id, source_version) "
                        "REFERENCES rag_source_snapshot(id, source_version))"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE rag_source_ingestion_run (id integer PRIMARY KEY, operation_id integer, "
                        "started_at timestamptz, snapshot_id integer, run_status text, failure_code text, "
                        "failure_message text, duration_ms integer, finished_at timestamptz)"
                    )
                )
                await connection.execute(text("INSERT INTO rag_source VALUES (1)"))
                await connection.execute(text("INSERT INTO rag_source_snapshot VALUES (1, 'external:synthetic-v1')"))
                await connection.execute(text("INSERT INTO rag_citation VALUES (1, 'external:synthetic-v1')"))
                await connection.execute(
                    text("INSERT INTO rag_source_ingestion_run (id, run_status) VALUES (1, 'FAILED')")
                )
                try:
                    yield connection
                finally:
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE rag_source SET max_rejected_records = 1",
        "UPDATE rag_source SET max_rejection_rate = 0.1",
        "UPDATE rag_source_snapshot SET external_version = 'synthetic-v1'",
    ],
)
async def test_policy_downgrade_refuses_before_changing_schema_or_data(statement):
    async with _isolated_schema() as connection:
        await connection.run_sync(_upgrade)
        await connection.execute(text(statement))
        before_source = (await connection.execute(text("SELECT * FROM rag_source"))).all()
        before_snapshot = (await connection.execute(text("SELECT * FROM rag_source_snapshot"))).all()
        with pytest.raises(RuntimeError, match="would be lost"):
            await connection.run_sync(_downgrade)
        assert (await connection.execute(text("SELECT * FROM rag_source"))).all() == before_source
        assert (await connection.execute(text("SELECT * FROM rag_source_snapshot"))).all() == before_snapshot
        async with connection.begin_nested() as savepoint:
            with pytest.raises(IntegrityError):
                await connection.execute(text("INSERT INTO rag_citation VALUES (999, 'missing')"))
            await savepoint.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assignment",
    [
        "attempted_source_version = 'external:synthetic-v1'",
        "attempted_external_version = 'synthetic-v1'",
        "attempted_canonical_contract = '{}'::jsonb",
        "validation_reason_code = 'SOURCE_VERSION_INVALID'",
        "invalid_source_version_sha256 = repeat('a', 64), invalid_source_version_byte_length = 0, "
        "validation_reason_code = 'SOURCE_VERSION_INVALID'",
    ],
)
async def test_attempt_downgrade_preserves_any_recorded_provenance(assignment):
    async with _isolated_schema() as connection:
        await connection.run_sync(_attempt_upgrade)
        await connection.execute(text(f"UPDATE rag_source_ingestion_run SET {assignment}"))
        before = (await connection.execute(text("SELECT * FROM rag_source_ingestion_run"))).all()
        with pytest.raises(RuntimeError, match="would be lost"):
            await connection.run_sync(_attempt_downgrade)
        assert (await connection.execute(text("SELECT * FROM rag_source_ingestion_run"))).all() == before
        assert await connection.scalar(text("SELECT to_regclass('idx_rag_ingestion_attempt_version')")) is not None


@pytest.mark.asyncio
async def test_both_revisions_roundtrip_preserves_legacy_rows_and_does_not_restore_public_update():
    async with _isolated_schema() as connection:
        # The grant only affects the temporary synthetic table and rolls back with it.
        await connection.execute(text("GRANT UPDATE ON rag_source_ingestion_run TO PUBLIC"))
        await connection.run_sync(_upgrade)
        await connection.run_sync(_attempt_upgrade)
        for downgrade in (_attempt_downgrade, _downgrade):
            await connection.run_sync(downgrade)
        assert await connection.scalar(text("SELECT source_version FROM rag_citation")) == "external:synthetic-v1"
        assert await connection.scalar(text("SELECT run_status FROM rag_source_ingestion_run")) == "FAILED"
        assert not await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a "
                "WHERE c.oid = 'rag_source_ingestion_run'::regclass AND a.grantee = 0 "
                "AND a.privilege_type = 'UPDATE')"
            )
        )
        await connection.run_sync(_upgrade)
        await connection.run_sync(_attempt_upgrade)
        assert await connection.scalar(text("SELECT max_rejected_records FROM rag_source")) == 0
        assert await connection.scalar(text("SELECT attempted_source_version FROM rag_source_ingestion_run")) is None


@pytest.mark.asyncio
async def test_policy_guard_rolls_back_an_earlier_attempt_downgrade_in_same_transaction():
    async with _isolated_schema() as connection:
        await connection.run_sync(_upgrade)
        await connection.run_sync(_attempt_upgrade)
        await connection.execute(text("UPDATE rag_source SET max_rejected_records = 1"))
        with pytest.raises(RuntimeError, match="would be lost"):
            async with connection.begin_nested():
                await connection.run_sync(_attempt_downgrade)
                await connection.run_sync(_downgrade)
        assert await connection.scalar(text("SELECT attempted_source_version FROM rag_source_ingestion_run")) is None
        assert await connection.scalar(text("SELECT to_regclass('idx_rag_ingestion_attempt_version')")) is not None
        assert await connection.scalar(text("SELECT max_rejected_records FROM rag_source")) == 1
