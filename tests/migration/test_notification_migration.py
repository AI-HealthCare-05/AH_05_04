"""Verify the actual migrated notification constraints and lossless rollback guard."""

import asyncio
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from tests.migration.test_medication_checkin_migration import _alembic_config, _connection
from tests.migration.test_medication_schedule_migration import _cleanup_graph, _seed_graph


@pytest.fixture(autouse=True)
def _isolated_notification_database(monkeypatch) -> Iterator[None]:
    """Keep the irreversible current head out of historical migration tests."""
    database = f"notification203_{uuid4().hex[:12]}"
    cluster_url = config.database_url

    async def database_action(create: bool) -> None:
        engine = create_async_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text(f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)')
                )
        finally:
            await engine.dispose()

    asyncio.run(database_action(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(database_action(False))


async def _insert_and_check(ids):
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    "INSERT INTO notification_record(id, occurrence_id, kind, scheduled_at, status, attempt) "
                    "VALUES (:id, :occurrence_id, 'SCHEDULED', now(), 'PENDING', 0)"
                ),
                {"id": str(uuid4()), "occurrence_id": ids["occurrence_id"]},
            )
        for sql in [
            "UPDATE notification_record SET attempt = 1",
            "UPDATE notification_record SET read_at = now()",
            "UPDATE notification_record SET status = 'UNKNOWN'",
            "UPDATE notification_record SET kind = 'OTHER'",
            "UPDATE notification_record SET status = 'DELIVERED', delivered_at = now(), attempt = 1, read_at = now() - interval '1 day'",
        ]:
            transaction = await connection.begin()
            try:
                with pytest.raises(DBAPIError):
                    await connection.execute(text(sql))
            finally:
                await transaction.rollback()
        async with connection.begin():
            with pytest.raises(DBAPIError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "INSERT INTO notification_record(id, occurrence_id, kind, scheduled_at, status, attempt) "
                            "VALUES (:id, :occurrence_id, 'SCHEDULED', now(), 'PENDING', 0)"
                        ),
                        {"id": str(uuid4()), "occurrence_id": ids["occurrence_id"]},
                    )


async def _cleanup(ids):
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM notification_record WHERE occurrence_id = :occurrence_id"), ids)
    await _cleanup_graph(ids)


async def _exists():
    async with _connection() as connection:
        return await connection.scalar(text("SELECT to_regclass('public.notification_record') IS NOT NULL"))


def test_notification_migration_constraints_and_rollback_guard():
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    ids = asyncio.run(_seed_graph())
    try:
        asyncio.run(_insert_and_check(ids))
        with pytest.raises(RuntimeError, match="notification history blocks downgrade"):
            command.downgrade(cfg, "165a0b1c2d3e")
        assert asyncio.run(_exists())
    finally:
        asyncio.run(_cleanup(ids))
    try:
        command.downgrade(cfg, "165a0b1c2d3e")
        assert not asyncio.run(_exists())
    finally:
        command.upgrade(cfg, "head")
    assert asyncio.run(_exists())
