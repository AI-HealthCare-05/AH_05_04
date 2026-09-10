"""PD-417 migration evidence and append-only constraints on PostgreSQL."""

import argparse
import asyncio
import json
import stat
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import config
from tests.migration.test_medication_schedule_migration import (
    _cleanup_graph,
    _connection,
    _seed_graph,
    create_alembic_config,
)


@pytest.fixture(autouse=True)
def isolated_migration_database(monkeypatch):
    # Never advance the shared historical-migration database past #398's irreversible boundary.
    database = "schedule423_" + uuid4().hex[:12]
    admin_url = make_url(config.database_url).set(database="postgres")

    async def manage(create: bool):
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                if create:
                    await connection.execute(text(f'CREATE DATABASE "{database}"'))
                else:
                    await connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        finally:
            await engine.dispose()

    asyncio.run(manage(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(manage(False))


async def _run(sql: str, values=None):
    async with _connection() as connection:
        async with connection.begin():
            result = await connection.execute(text(sql), values or {})
            return result.scalar() if result.returns_rows else None


def test_baseline_requires_private_artifact_and_preserves_unknown_history(tmp_path: Path) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "3984b5c6d7e8")
    ids = asyncio.run(_seed_graph())
    try:
        asyncio.run(_run("UPDATE medication_occurrence SET status='CANCELLED' WHERE id=:occurrence_id", ids))
        with pytest.raises(RuntimeError, match="schedule_baseline_path"):
            command.upgrade(cfg, "head")
        assert asyncio.run(_run("SELECT version_num FROM alembic_version")) == "3984b5c6d7e8"
        destination = tmp_path / "baseline.json"
        cfg.cmd_opts = argparse.Namespace(x=[f"schedule_baseline_path={destination}"])
        tmp_path.chmod(0o755)
        with pytest.raises(RuntimeError, match="private"):
            command.upgrade(cfg, "head")
        tmp_path.chmod(0o700)
        command.upgrade(cfg, "head")
        baseline = json.loads(destination.read_text())
        assert baseline["kind"] == "MIGRATION_BASELINE_NOT_MUTATION_HISTORY"
        assert baseline["database_commit_asserted"] is False
        assert baseline["schedules"][0]["id"] == ids["schedule_id"]
        assert baseline["schedules"][0]["revision"] == 1
        assert baseline["schedules"][0]["local_times"] == ["09:00:00"]
        assert "changed_by" not in baseline["schedules"][0]
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        assert asyncio.run(_run("SELECT count(*) FROM medication_schedule_audit")) == 0
        assert asyncio.run(_run("SELECT cancelled_at FROM medication_occurrence WHERE id=:occurrence_id", ids)) is None
        command.downgrade(cfg, "3984b5c6d7e8")
        with pytest.raises(FileExistsError):
            command.upgrade(cfg, "head")
        # Existing evidence is never overwritten by a retry.
        assert json.loads(destination.read_text()) == baseline
    finally:
        asyncio.run(_cleanup_graph(ids))
        command.upgrade(cfg, "head")


def test_audit_actor_revision_fk_and_downgrade_guards() -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "head")
    ids = asyncio.run(_seed_graph())
    try:
        parameters = {**ids, "audit_id": str(uuid4())}
        statement = """INSERT INTO medication_schedule_audit
            (id, medication_schedule_id, from_revision, to_revision, before_snapshot, after_snapshot,
             changed_by, change_source, changed_at)
            VALUES (:audit_id, :schedule_id, 1, 2, '{}'::jsonb, '{}'::jsonb, :user_id, 'USER', now())"""
        asyncio.run(_run(statement, parameters))
        assert (
            asyncio.run(
                _run(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='medication_schedule_audit'::regclass AND NOT tgisinternal"
                )
            )
            == 0
        )
        with pytest.raises(RuntimeError, match="history exists"):
            command.downgrade(cfg, "3984b5c6d7e8")
        for bad_sql, constraint in [
            (statement.replace("1, 2,", "1, 3,"), "revision_step"),
            (statement.replace(":user_id, 'USER'", "NULL, 'USER'"), "actor"),
            (statement.replace("'{}'::jsonb, '{}'::jsonb", "NULL, '{}'::jsonb"), "before_snapshot"),
        ]:
            with pytest.raises(DBAPIError, match=constraint):
                asyncio.run(_run(bad_sql, {**parameters, "audit_id": str(uuid4())}))
        with pytest.raises(DBAPIError, match="uq_schedule_audit_to_revision"):
            asyncio.run(_run(statement, {**parameters, "audit_id": str(uuid4())}))
    finally:
        # Test-only admin teardown; normal runtime has no history erasure path.
        asyncio.run(_run("TRUNCATE medication_schedule_audit"))
        asyncio.run(_cleanup_graph(ids))


def test_cancellation_instant_alone_blocks_downgrade() -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "head")
    ids = asyncio.run(_seed_graph())
    try:
        asyncio.run(
            _run("UPDATE medication_occurrence SET status='CANCELLED', cancelled_at=now() WHERE id=:occurrence_id", ids)
        )
        with pytest.raises(RuntimeError, match="history exists"):
            command.downgrade(cfg, "3984b5c6d7e8")
    finally:
        asyncio.run(_cleanup_graph(ids))
    command.downgrade(cfg, "3984b5c6d7e8")
    command.upgrade(cfg, "head")


def test_concurrent_corrections_allow_only_one_new_active_version_on_migrated_postgresql() -> None:
    # Current correction services use the latest occurrence schema, including cancelled_at.
    from tests.migration.test_prescription_version_backfill_migration import (
        _cleanup,
        _correct_concurrently,
        _create_via_repository,
        _snapshot,
        _version_counts,
    )

    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_create_via_repository())
    try:
        snapshot = asyncio.run(_snapshot(ids))
        assert snapshot is not None
        base_version_id = UUID(str(snapshot["active_version_id"]))

        results = asyncio.run(_correct_concurrently(ids, base_version_id=base_version_id))

        assert [code for code, _ in results] == ["ok", "ok"]
        assert results[0][1] == results[1][1]
        assert asyncio.run(_version_counts(ids)) == (2, 2)
        active = asyncio.run(_snapshot(ids))
        assert active is not None
        assert active["version_number"] == 2
        assert str(active["active_version_id"]) in {version_id for _, version_id in results if version_id}
    finally:
        asyncio.run(_cleanup(ids))
