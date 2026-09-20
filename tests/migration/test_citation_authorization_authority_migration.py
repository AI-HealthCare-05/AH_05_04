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
MIGRATION_PATH = PROJECT_ROOT / "backend/alembic/versions/869a1b2c3d4e_citation_authorization_authority.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("citation_authorization_authority_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend/alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    database = f"citation_authority869_{uuid4().hex[:10]}"
    cluster_url = config.database_url

    async def database_action(create: bool) -> None:
        engine = create_async_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                sql = f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)'
                await connection.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(database_action(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(database_action(False))


async def _state() -> tuple[str, tuple[bool, ...], int]:
    tables = (
        "rag_citation_authorization_source_decision",
        "rag_citation_authorization_member_decision",
        "rag_citation_authorization_receipt",
        "rag_citation_authorization_receipt_selection",
    )
    async with _connection() as connection:
        revision = str(await connection.scalar(text("SELECT version_num FROM alembic_version")))
        observed: list[bool] = []
        for table in tables:
            observed.append(await connection.scalar(text(f"SELECT to_regclass('public.{table}')")) is not None)
        exists = tuple(observed)
        count = (
            0
            if not exists[2]
            else int(await connection.scalar(text("SELECT count(*) FROM rag_citation_authorization_receipt")))
        )
        return revision, exists, count


async def _seed_receipt() -> None:
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text(
                """
                INSERT INTO rag_citation_authorization_receipt (
                    id, artifact_code, artifact_version, artifact_content_sha256, request_sha256,
                    origin_guard_artifact_code, origin_guard_artifact_version, origin_guard_content_sha256,
                    origin_decision, operation, environment, bundle_id, bundle_manifest_hash,
                    request_scope_codes, scope_manifest_hash, validated_selection_sha256,
                    selection_manifest_sha256
                ) VALUES (
                    :id, 'citation_authorization_receipt', '1.0', :a, :b,
                    'request_guard_runtime_binding', '1.0', :c,
                    'PASS', 'CITATION_AUTHORIZATION', 'TEST', :bundle, :d,
                    '["GUIDE", "PATIENT_CITATION"]'::jsonb, :e, :f, :g
                )
                """
            ),
            {
                "id": str(uuid4()),
                "bundle": str(uuid4()),
                "a": "a" * 64,
                "b": "b" * 64,
                "c": "c" * 64,
                "d": "d" * 64,
                "e": "e" * 64,
                "f": "f" * 64,
                "g": "0" * 64,
            },
        )


def test_revision_parent_is_actual_develop_head() -> None:
    migration = _load_migration()
    assert migration.revision == "869a1b2c3d4e"
    assert migration.down_revision == "853a1b2c3d4e"


def test_populated_downgrade_preserves_all_tables_row_and_revision(isolated_database: None) -> None:
    migration = _load_migration()
    cfg = _alembic_config()
    command.upgrade(cfg, migration.revision)
    asyncio.run(_seed_receipt())

    with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
        command.downgrade(cfg, migration.down_revision)

    assert asyncio.run(_state()) == (migration.revision, (True, True, True, True), 1)


def test_empty_downgrade_and_reupgrade_succeed(isolated_database: None) -> None:
    migration = _load_migration()
    cfg = _alembic_config()
    command.upgrade(cfg, migration.revision)
    assert asyncio.run(_state()) == (migration.revision, (True, True, True, True), 0)

    command.downgrade(cfg, migration.down_revision)
    assert asyncio.run(_state()) == (migration.down_revision, (False, False, False, False), 0)

    command.upgrade(cfg, migration.revision)
    assert asyncio.run(_state()) == (migration.revision, (True, True, True, True), 0)
