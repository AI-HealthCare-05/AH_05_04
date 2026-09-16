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
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "206b2c3d4e5f_create_account_deletion_request.py"

_SPEC = importlib.util.spec_from_file_location("account_deletion_request_migration", MIGRATION_PATH)
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
async def _isolated_account_deletion_request_schema() -> AsyncIterator[AsyncConnection]:
    schema_name = f"account_deletion_request_{uuid4().hex}"
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}"'))
                # account_deletion_request.user_id FK가 참조하는 최소 형태의 "user" 테이블입니다.
                await connection.execute(text('CREATE TABLE "user" (id char(36) PRIMARY KEY)'))
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _table_exists(connection: AsyncConnection) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_tables
                WHERE schemaname = current_schema()
                  AND tablename = 'account_deletion_request'
            )
            """
        )
    )
    return bool(result.scalar_one())


async def _seed_user_and_request(connection: AsyncConnection) -> None:
    user_id = str(uuid4())
    await connection.execute(text('INSERT INTO "user" (id) VALUES (:id)'), {"id": user_id})
    await connection.execute(
        text(
            "INSERT INTO account_deletion_request "
            "(id, user_id, status, requested_at, retry_count, created_at, updated_at) "
            "VALUES (:id, :user_id, 'PENDING', now(), 0, now(), now())"
        ),
        {"id": str(uuid4()), "user_id": user_id},
    )


async def _account_deletion_request_count(connection: AsyncConnection) -> int:
    result = await connection.execute(text("SELECT COUNT(*) FROM account_deletion_request"))
    return int(result.scalar_one())


def test_account_deletion_request_downgrade_removes_table_when_empty() -> None:
    async def run() -> None:
        async with _isolated_account_deletion_request_schema() as connection:
            await connection.run_sync(_upgrade)
            assert await _table_exists(connection)

            await connection.run_sync(_downgrade)

            assert not await _table_exists(connection)

    asyncio.run(run())


def test_account_deletion_request_downgrade_blocks_when_data_exists_and_preserves_schema() -> None:
    async def run() -> None:
        async with _isolated_account_deletion_request_schema() as connection:
            await connection.run_sync(_upgrade)
            await _seed_user_and_request(connection)

            with pytest.raises(RuntimeError, match="Cannot downgrade revision 206b2c3d4e5f"):
                await connection.run_sync(_downgrade)

            assert await _table_exists(connection)
            assert await _account_deletion_request_count(connection) == 1

    asyncio.run(run())
