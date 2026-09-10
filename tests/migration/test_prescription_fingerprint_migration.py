"""Fingerprint expansion uses a disposable database with the full migration history."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from tests.migration.test_medication_schedule_migration import _connection, _seed_graph


async def _database(url, name, *, create):
    engine = create_async_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(f'CREATE DATABASE "{name}"' if create else f'DROP DATABASE "{name}" WITH (FORCE)')
            )
    finally:
        await engine.dispose()


async def _columns():
    async with _connection() as connection:
        return set(
            (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name='prescription_version' AND table_schema='public'"
                    )
                )
            ).scalars()
        )


async def _assert_backfill(ids):
    async with _connection() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT v.medication_count, v.content_hash, m.medication_count FROM prescription_version v "
                    "JOIN prescription_version_medication m ON m.prescription_version_id=v.id WHERE v.id=:id"
                ),
                {"id": ids["version_id"]},
            )
        ).one()
        assert row[0] == row[2] == 1
        assert len(row[1]) == 64
        guards = (
            (
                await connection.execute(
                    text(
                        "SELECT tgenabled::text FROM pg_trigger WHERE tgname IN "
                        "('trg_prescription_version_prevent_update', 'trg_prescription_version_medication_prevent_update')"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert guards == ["O", "O"]


async def _make_invalid(ids):
    async with _connection() as connection, connection.begin():
        await connection.execute(
            text(
                "ALTER TABLE prescription_version_medication DISABLE TRIGGER trg_prescription_version_medication_prevent_update"
            )
        )
        await connection.execute(
            text("UPDATE prescription_version_medication SET display_order=2 WHERE id=:id"),
            {"id": ids["version_medication_id"]},
        )
        await connection.execute(
            text(
                "ALTER TABLE prescription_version_medication ENABLE TRIGGER trg_prescription_version_medication_prevent_update"
            )
        )


@pytest.mark.parametrize("scenario", ["empty", "existing", "invalid"])
def test_fingerprint_expand_backfill_and_rollback(monkeypatch, scenario):
    admin_url = config.database_url
    database = f"synthetic398_{uuid4().hex[:12]}"
    asyncio.run(_database(admin_url, database, create=True))
    monkeypatch.setattr(config, "DB_NAME", database)
    alembic = Config(str(Path(__file__).resolve().parents[2] / "backend/alembic.ini"))
    try:
        command.upgrade(alembic, "201a1b2c3d4e")
        ids = asyncio.run(_seed_graph()) if scenario != "empty" else None
        if scenario == "invalid":
            asyncio.run(_make_invalid(ids))
            with pytest.raises(RuntimeError, match="backfill refused"):
                command.upgrade(alembic, "398a1b2c3d4e")
            assert "content_hash" not in asyncio.run(_columns())
        else:
            command.upgrade(alembic, "398a1b2c3d4e")
            assert "content_hash" in asyncio.run(_columns())
            if ids:
                asyncio.run(_assert_backfill(ids))
            command.downgrade(alembic, "201a1b2c3d4e")
            assert "content_hash" not in asyncio.run(_columns())
    finally:
        asyncio.run(_database(admin_url, database, create=False))
