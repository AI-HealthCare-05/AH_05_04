"""Both previously published migration heads converge without rewriting their history."""

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


@pytest.mark.parametrize("previous", ["362b2c3d4e5f", "175a1b2c3d4e"])
def test_source_and_runtime_heads_merge_preserving_existing_source(previous, monkeypatch):
    original = config.database_url
    name = "merge436_" + uuid4().hex
    url = make_url(original).set(database=name)

    async def database_command(sql):
        engine = create_async_engine(original, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(text(sql))
        finally:
            await engine.dispose()

    async def source_rows(*, seed=False):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                if seed:
                    await connection.execute(
                        text(
                            "INSERT INTO rag_source (id,source_code,display_name,lifecycle_status) "
                            "VALUES (:id,'SYNTHETIC_MERGE','합성 출처','DRAFT')"
                        ),
                        {"id": str(uuid4())},
                    )
                return [dict(row) for row in (await connection.execute(text("SELECT * FROM rag_source"))).mappings()]
        finally:
            await engine.dispose()

    async def verify():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                heads = migration_heads()
                assert heads == ("362c3d4e5f60",)
                assert validation_errors(heads[0], await read_database_head_state(connection)) == []
        finally:
            await engine.dispose()

    asyncio.run(database_command(f'CREATE DATABASE "{name}"'))
    try:
        monkeypatch.setattr(config, "DB_NAME", name)
        cfg = Config(str(ROOT / "backend/alembic.ini"))
        command.upgrade(cfg, previous)
        before = asyncio.run(source_rows(seed=True))
        command.upgrade(cfg, "head")
        after = asyncio.run(source_rows())
        assert [{key: row[key] for key in before[0]} for row in after] == before
        asyncio.run(verify())
    finally:
        asyncio.run(database_command(f'DROP DATABASE "{name}" WITH (FORCE)'))
