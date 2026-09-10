"""Run the forward migration against isolated synthetic legacy rows."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

_PATH = Path(__file__).resolve().parents[2] / "backend/alembic/versions/362a1b2c3d4e_source_policy_storage.py"
_SPEC = importlib.util.spec_from_file_location("source_policy_storage_362", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)


def _upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        _MIGRATION.upgrade()


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
                    with pytest.raises(RuntimeError, match="forward-fix"):
                        _MIGRATION.downgrade()
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
