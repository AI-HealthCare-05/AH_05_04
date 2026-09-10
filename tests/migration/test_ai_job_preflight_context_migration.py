from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_CONTEXT_REVISION = "174a1b2c3d4e"
PREFLIGHT_CONTEXT_BASE_REVISION = "201a1b2c3d4e"
PREFLIGHT_CONTEXT_TABLES = {
    "ai_job_intake_context",
    "ai_job_execution_context",
    "ai_job_execution_identification",
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
            {"table_names": list(PREFLIGHT_CONTEXT_TABLES)},
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
            {"table_names": list(PREFLIGHT_CONTEXT_TABLES)},
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
            {"table_names": list(PREFLIGHT_CONTEXT_TABLES)},
        )
        return {*(row[0] for row in constraints), *(row[0] for row in indexes)}


async def _cleanup_context_tables() -> None:
    async with _connection() as connection:
        async with connection.begin():
            for table_name in (
                "ai_job_execution_identification",
                "ai_job_execution_context",
                "ai_job_intake_context",
            ):
                table_exists = await connection.execute(
                    text("SELECT to_regclass(:table_name)"), {"table_name": table_name}
                )
                if table_exists.scalar_one() is not None:
                    await connection.execute(text(f"DELETE FROM {table_name}"))


@pytest.fixture(autouse=True)
def _clean_context_data_after_test() -> Iterator[None]:
    yield
    asyncio.run(_cleanup_context_tables())


def _upgrade_to_preflight_context() -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "head")
    asyncio.run(_cleanup_context_tables())
    command.downgrade(cfg, PREFLIGHT_CONTEXT_BASE_REVISION)
    command.upgrade(cfg, PREFLIGHT_CONTEXT_REVISION)


def test_preflight_context_upgrade_creates_tables_and_contract_constraints() -> None:
    _upgrade_to_preflight_context()

    table_names = asyncio.run(_fetch_table_names())
    schema_objects = asyncio.run(_fetch_schema_object_names())

    assert table_names == PREFLIGHT_CONTEXT_TABLES
    assert "uq_ai_job_intake_context_job" in schema_objects
    assert "uq_ai_job_intake_context_chat_message" in schema_objects
    assert "fk_ai_job_intake_context_bundle_manifest" in schema_objects
    assert "fk_ai_job_execution_context_bundle_manifest" in schema_objects
    assert "chk_ai_job_execution_context_one_domain" in schema_objects
    assert "chk_ai_job_execution_context_intake_chat_only" in schema_objects
    assert "uq_ai_job_execution_identification_medication" in schema_objects
    assert "idx_ai_job_execution_identification_context" in schema_objects


def test_preflight_context_downgrade_empty_schema_removes_tables() -> None:
    cfg = create_alembic_config()
    _upgrade_to_preflight_context()

    command.downgrade(cfg, PREFLIGHT_CONTEXT_BASE_REVISION)

    assert asyncio.run(_fetch_table_names()) == set()
    command.upgrade(cfg, "head")
