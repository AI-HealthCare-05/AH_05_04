"""#180 execution-context #806 pin migration metadata checks."""

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
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "180a1b2c3d4e_pin_execution_request_guard_runtime_binding.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("execution_request_guard_runtime_binding_pin", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    """Avoid locale-decoding the user-facing ini comments during this isolated schema test."""
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "alembic"))
    alembic_config.set_main_option("path_separator", "os")
    alembic_config.set_main_option("prepend_sys_path", str(PROJECT_ROOT / "backend"))
    return alembic_config


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
def isolated_pin_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    database = f"guide_request_pin_{uuid4().hex[:12]}"
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


async def _pin_schema_state() -> tuple[set[str], set[str]]:
    async with _connection() as connection:
        columns = await connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ai_job_execution_context'
                  AND column_name LIKE 'request_guard_runtime_binding_%'
                """
            )
        )
        constraints = await connection.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = 'ai_job_execution_context'
                  AND constraint_name IN (
                    'chk_ai_job_exec_ctx_request_guard_pin_all_or_none',
                    'fk_ai_job_execution_context_request_guard_runtime_binding'
                  )
                """
            )
        )
        return {str(row[0]) for row in columns}, {str(row[0]) for row in constraints}


def test_migration_has_current_single_parent() -> None:
    migration = _load_migration()

    assert migration.revision == "180a1b2c3d4e"
    assert migration.down_revision == "921a1b2c3d4e"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_migration_pins_all_three_request_guard_runtime_binding_identity_columns() -> None:
    migration_text = MIGRATION_PATH.read_text(encoding="utf-8")

    for fragment in (
        "request_guard_runtime_binding_artifact_code",
        "request_guard_runtime_binding_artifact_version",
        "request_guard_runtime_binding_content_sha256",
        "rag_request_guard_runtime_binding",
        'ondelete="RESTRICT"',
    ):
        assert fragment.lower() in migration_text.lower()
    assert "IS NULL" in migration_text
    assert "IS NOT NULL" in migration_text


def test_migration_creates_and_removes_typed_request_guard_runtime_pin(isolated_pin_database: None) -> None:
    migration = _load_migration()
    expected_columns = {
        "request_guard_runtime_binding_artifact_code",
        "request_guard_runtime_binding_artifact_version",
        "request_guard_runtime_binding_content_sha256",
    }
    expected_constraints = {
        "chk_ai_job_exec_ctx_request_guard_pin_all_or_none",
        "fk_ai_job_execution_context_request_guard_runtime_binding",
    }

    alembic_config = _alembic_config()
    command.upgrade(alembic_config, migration.revision)
    assert asyncio.run(_pin_schema_state()) == (expected_columns, expected_constraints)

    command.downgrade(alembic_config, migration.down_revision)
    assert asyncio.run(_pin_schema_state()) == (set(), set())


@pytest.mark.parametrize(("forbidden",), (("CREATE " + "TRIGGER",), ("CREATE " + "POLICY",), ("CREATE " + "FUNCTION",)))
def test_migration_does_not_add_database_business_logic(forbidden: str) -> None:
    assert forbidden not in MIGRATION_PATH.read_text(encoding="utf-8").upper()
