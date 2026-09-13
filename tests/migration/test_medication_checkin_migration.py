"""Track B Check-in PostgreSQL migration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from tests.migration.test_medication_schedule_migration import _cleanup_graph, _seed_graph

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKIN_REVISION = "201a1b2c3d4e"
CHECKIN_BASE_REVISION = "164c5d6e7f8a"
CHECKIN_TABLES = {"medication_checkin", "checkin_audit"}


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


async def _schema_snapshot() -> tuple[set[str], set[str], set[str]]:
    async with _connection() as connection:
        tables = await connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
            ),
            {"tables": list(CHECKIN_TABLES)},
        )
        columns = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
            ),
            {"tables": list(CHECKIN_TABLES)},
        )
        constraints = await connection.execute(
            text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
            ),
            {"tables": list(CHECKIN_TABLES)},
        )
        return (
            {row[0] for row in tables},
            {row[0] for row in columns},
            {row[0] for row in constraints},
        )


def test_checkin_migration_upgrade_and_empty_downgrade() -> None:
    alembic_config = _alembic_config()
    command.upgrade(alembic_config, "398b2c3d4e5f")
    try:
        tables, columns, constraints = asyncio.run(_schema_snapshot())
        assert tables == CHECKIN_TABLES
        assert "reason_code" not in columns
        assert {
            "uq_medication_checkin_occurrence_id",
            "uq_checkin_audit_checkin_to_revision",
            "chk_medication_checkin_status",
            "chk_checkin_audit_revision_step",
        } <= constraints

        command.downgrade(alembic_config, CHECKIN_BASE_REVISION)
        tables, _, _ = asyncio.run(_schema_snapshot())
        assert tables == set()
    finally:
        command.upgrade(alembic_config, "398b2c3d4e5f")


async def _seed_checkin_audit(ids: dict[str, str]) -> dict[str, str]:
    checkin_ids = {
        **ids,
        "checkin_id": str(uuid4()),
        "audit_id": str(uuid4()),
    }
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    "INSERT INTO medication_checkin "
                    "(id, occurrence_id, status, taken_at, revision) "
                    "VALUES (:checkin_id, :occurrence_id, 'NOT_TAKEN', NULL, 2)"
                ),
                checkin_ids,
            )
            await connection.execute(
                text(
                    "INSERT INTO checkin_audit "
                    "(id, checkin_id, from_status, to_status, from_revision, to_revision, changed_by, changed_at) "
                    "VALUES (:audit_id, :checkin_id, 'UNCONFIRMED', 'NOT_TAKEN', 1, 2, :user_id, :changed_at)"
                ),
                {**checkin_ids, "changed_at": datetime(2026, 9, 9, tzinfo=UTC)},
            )
    return checkin_ids


async def _assert_audit_is_append_only(ids: dict[str, str]) -> None:
    async with _connection() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError, match="checkin_audit rows are append-only"):
                await connection.execute(
                    text("UPDATE checkin_audit SET to_status = 'TAKEN' WHERE id = :audit_id"),
                    ids,
                )
        finally:
            await transaction.rollback()


async def _truncate_checkin_history() -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("TRUNCATE TABLE checkin_audit, medication_checkin"))


def test_checkin_audit_is_append_only_and_history_blocks_downgrade() -> None:
    alembic_config = _alembic_config()
    command.upgrade(alembic_config, "398b2c3d4e5f")
    graph_ids = asyncio.run(_seed_graph())
    ids = asyncio.run(_seed_checkin_audit(graph_ids))
    try:
        asyncio.run(_assert_audit_is_append_only(ids))
        with pytest.raises(RuntimeError, match=f"Cannot downgrade revision {CHECKIN_REVISION}"):
            command.downgrade(alembic_config, CHECKIN_BASE_REVISION)
    finally:
        asyncio.run(_truncate_checkin_history())
        asyncio.run(_cleanup_graph(graph_ids))
        command.upgrade(alembic_config, "398b2c3d4e5f")
