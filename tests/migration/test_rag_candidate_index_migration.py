from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "583a1b2c3d4f_candidate_index_lifecycle.py"
READY_INDEX_NAME = "uq_rag_candidate_index_ready_per_code"

_SPEC = importlib.util.spec_from_file_location("candidate_index_lifecycle_migration", MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)


def _upgrade(connection) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        _MIGRATION.upgrade()


def _downgrade(connection) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        _MIGRATION.downgrade()


@asynccontextmanager
async def _isolated_candidate_index_schema() -> AsyncIterator[AsyncConnection]:
    schema_name = f"candidate_index_lifecycle_{uuid4().hex}"
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}"'))
                await connection.execute(
                    text(
                        """
                        CREATE TABLE rag_candidate_index_version (
                            id uuid PRIMARY KEY,
                            index_code varchar(64) NOT NULL,
                            status varchar(32) NOT NULL
                        )
                        """
                    )
                )
                await connection.execute(
                    text(
                        """
                        CREATE TABLE rag_candidate_index_member (
                            id uuid PRIMARY KEY,
                            embedding double precision[]
                        )
                        """
                    )
                )
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _index_exists(connection: AsyncConnection) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = :index_name
            )
            """
        ),
        {"index_name": READY_INDEX_NAME},
    )
    return bool(result.scalar_one())


async def _candidate_index_version_count(connection: AsyncConnection) -> int:
    result = await connection.execute(text("SELECT COUNT(*) FROM rag_candidate_index_version"))
    return int(result.scalar_one())


async def _seed_candidate_index_version(connection: AsyncConnection) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO rag_candidate_index_version (id, index_code, status)
            VALUES (:id, :index_code, 'BUILDING')
            """
        ),
        {"id": uuid4(), "index_code": f"migration-index-{uuid4().hex[:10]}"},
    )


def test_candidate_index_ready_partial_unique_exists_after_upgrade() -> None:
    async def run() -> None:
        async with _isolated_candidate_index_schema() as connection:
            await connection.run_sync(_upgrade)

            assert await _index_exists(connection)

    asyncio.run(run())


def test_candidate_index_empty_downgrade_removes_ready_guard() -> None:
    async def run() -> None:
        async with _isolated_candidate_index_schema() as connection:
            await connection.run_sync(_upgrade)

            await connection.run_sync(_downgrade)

            assert not await _index_exists(connection)

    asyncio.run(run())


def test_candidate_index_downgrade_blocks_when_data_exists_and_preserves_schema() -> None:
    async def run() -> None:
        async with _isolated_candidate_index_schema() as connection:
            await connection.run_sync(_upgrade)
            await _seed_candidate_index_version(connection)

            with pytest.raises(RuntimeError, match="Candidate Index lifecycle records exist"):
                await connection.run_sync(_downgrade)

            assert await _index_exists(connection)
            assert await _candidate_index_version_count(connection) == 1

    asyncio.run(run())
