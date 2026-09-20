from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "921a1b2c3d4e_guide_retrieval_binding_manifest.py"
TABLE = "guide_retrieval_binding_manifest"
PIN_COLUMNS = {
    "guide_retrieval_binding_manifest_id",
    "guide_retrieval_binding_manifest_hash",
}


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("guide_retrieval_binding_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
def isolated_binding_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    database = f"guide_binding_{uuid4().hex[:12]}"
    cluster_url = config.database_url

    async def database_action(create: bool) -> None:
        engine = create_async_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                statement = f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)'
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(database_action(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(database_action(False))


async def _schema_state() -> tuple[bool, set[str]]:
    async with _connection() as connection:
        table_exists = bool(
            (
                await connection.execute(
                    text("SELECT to_regclass('public.guide_retrieval_binding_manifest') IS NOT NULL")
                )
            ).scalar_one()
        )
        result = await connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ai_job_execution_context'
                  AND column_name = ANY(:column_names)
                """
            ),
            {"column_names": list(PIN_COLUMNS)},
        )
        return table_exists, {str(row[0]) for row in result}


def test_migration_creates_and_removes_binding_authority(isolated_binding_database: None) -> None:
    migration = _load_migration()
    cfg = _alembic_config()
    command.upgrade(cfg, migration.revision)

    assert asyncio.run(_schema_state()) == (True, PIN_COLUMNS)

    command.downgrade(cfg, migration.down_revision)
    assert asyncio.run(_schema_state()) == (False, set())


def test_downgrade_refuses_to_drop_persisted_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()

    class _Result:
        def scalar_one(self) -> int:
            return 1

    class _Connection:
        def execute(self, _statement: object) -> _Result:
            return _Result()

    monkeypatch.setattr(migration.op, "get_bind", _Connection)

    with pytest.raises(RuntimeError, match="existing authority data"):
        migration.downgrade()
