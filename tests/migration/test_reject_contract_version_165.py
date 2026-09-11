"""Upgrade/downgrade, legacy preservation and least-privilege contract version checks."""

import asyncio
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from scripts.ci.verify_database_head import ROOT, migration_heads, read_database_head_state, validation_errors

REVISION = "165a0b1c2d3e"
PARENT = "362c3d4e5f60"


@pytest.fixture
def database(monkeypatch):
    original = config.database_url
    name = "reject165_migration_" + uuid4().hex
    url = make_url(original).set(database=name)

    async def execute(sql, params=None, *, admin=False, fetch=False):
        engine = create_async_engine(
            original if admin else url, poolclass=NullPool, isolation_level="AUTOCOMMIT" if admin else "READ COMMITTED"
        )
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params or {})
                return result.mappings().all() if fetch else None
        finally:
            await engine.dispose()

    asyncio.run(execute(f'CREATE DATABASE "{name}"', admin=True))
    monkeypatch.setattr(config, "DB_NAME", name)
    cfg = Config(str(ROOT / "backend/alembic.ini"))
    try:
        command.upgrade(cfg, PARENT)
        asyncio.run(
            execute("""
            INSERT INTO rag_source (id,source_code,display_name,lifecycle_status) VALUES ('s','SYNTHETIC','Synthetic','DRAFT');
        """)
        )
        asyncio.run(
            execute(
                "INSERT INTO rag_source_endpoint (id,source_id,endpoint_code,display_name,lifecycle_status,runtime_status,acquisition_status) VALUES ('e','s','SYNTHETIC','Synthetic','DRAFT','DISABLED','PENDING')"
            )
        )
        asyncio.run(
            execute(
                "INSERT INTO rag_source_operation (id,endpoint_id,operation_code,display_name,runtime_status,acquisition_status) VALUES ('o','e','SYNTHETIC','Synthetic','DISABLED','PENDING')"
            )
        )
        asyncio.run(
            execute(
                "INSERT INTO rag_source_ingestion_run (id,operation_id,run_group_key,attempt_number,run_status,started_at) VALUES ('r','o','legacy',1,'FAILED',now())"
            )
        )
        yield cfg, execute, url
    finally:
        asyncio.run(execute(f'DROP DATABASE "{name}" WITH (FORCE)', admin=True))


def test_upgrade_preserves_legacy_null_and_roundtrips_when_empty(database):
    cfg, execute, _ = database
    before = asyncio.run(execute("SELECT * FROM rag_source_ingestion_run", fetch=True))[0]
    command.upgrade(cfg, REVISION)
    after = asyncio.run(execute("SELECT * FROM rag_source_ingestion_run", fetch=True))[0]
    assert after["reject_code_contract_version"] is None
    assert {key: after[key] for key in before} == dict(before)
    command.downgrade(cfg, PARENT)
    assert dict(asyncio.run(execute("SELECT * FROM rag_source_ingestion_run", fetch=True))[0]) == dict(before)
    command.upgrade(cfg, "head")


def test_versioned_history_blocks_downgrade_without_changing_rows(database):
    cfg, execute, _ = database
    command.upgrade(cfg, REVISION)
    asyncio.run(execute("UPDATE rag_source_ingestion_run SET reject_code_contract_version='source-reject-codes@1'"))
    with pytest.raises(RuntimeError, match="history must be preserved"):
        command.downgrade(cfg, PARENT)
    row = asyncio.run(execute("SELECT reject_code_contract_version FROM rag_source_ingestion_run", fetch=True))[0]
    assert row["reject_code_contract_version"] == "source-reject-codes@1"
    assert asyncio.run(execute("SELECT version_num FROM alembic_version", fetch=True))[0]["version_num"] == REVISION


def test_lifecycle_role_cannot_change_contract_version(database):
    cfg, execute, url = database
    role = "reject165_role_" + uuid4().hex
    asyncio.run(execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER', admin=True))
    try:
        # Same column-level lifecycle boundary as #362; no table-wide UPDATE.
        asyncio.run(execute(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        asyncio.run(execute(f'GRANT SELECT ON rag_source_ingestion_run TO "{role}"'))
        asyncio.run(
            execute(f'GRANT UPDATE (run_status, failure_code, finished_at) ON rag_source_ingestion_run TO "{role}"')
        )
        command.upgrade(cfg, "head")

        async def verify():
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with engine.begin() as conn:
                    heads = migration_heads()
                    assert len(heads) == 1
                    assert validation_errors(heads[0], await read_database_head_state(conn)) == []
                    await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                    await conn.execute(text("UPDATE rag_source_ingestion_run SET run_status='FAILED'"))
                with pytest.raises(Exception, match="permission denied"):
                    async with engine.begin() as conn:
                        await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                        await conn.execute(
                            text(
                                "UPDATE rag_source_ingestion_run SET reject_code_contract_version='source-reject-codes@1'"
                            )
                        )
            finally:
                await engine.dispose()

        asyncio.run(verify())
    finally:
        asyncio.run(execute(f'DROP OWNED BY "{role}"'))
        asyncio.run(execute(f'DROP ROLE "{role}"', admin=True))
