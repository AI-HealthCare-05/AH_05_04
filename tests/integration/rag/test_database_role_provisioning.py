"""Real psql bootstrap and separate credential provisioning in a disposable database."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import config
from infra.python.provision_database_roles import (
    RUNTIME_APPEND_ONLY_TABLES,
    RUNTIME_MUTABLE_TABLES,
    run_provisioning,
)
from infra.python.source_role_policy import SOURCE_TABLES

ROOT = Path(__file__).resolve().parents[3]


async def test_bootstrap_then_provision_and_redeploy_do_not_reopen_permissions() -> None:
    container = os.environ.get("ISSUE398_TEST_POSTGRES_CONTAINER")
    if not container or not shutil.which("docker"):
        pytest.skip("Requires an explicitly selected disposable PostgreSQL container")
    suffix = uuid4().hex[:12]
    database = f"provision398_{suffix}"
    owner, runtime, writer = (f"provision398_{part}_{suffix}" for part in ("owner", "runtime", "writer"))
    password = "synthetic-provision398-only"
    url = URL.create(
        "postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_EXPOSE_PORT,
        database=config.DB_NAME,
    )
    cluster = create_async_engine(url, isolation_level="AUTOCOMMIT")
    admin = create_async_engine(url.set(database=database))
    reader = create_async_engine(url.set(database=database, username=runtime, password=password))
    producer = create_async_engine(url.set(database=database, username=writer, password=password))
    environment = {
        "DB_HOST": config.DB_HOST,
        "DB_PORT": str(config.DB_EXPOSE_PORT),
        "DB_NAME": database,
        "DB_ADMIN_USER": config.DB_USER,
        "DB_ADMIN_PASSWORD": config.DB_PASSWORD,
        "DB_MIGRATION_USER": owner,
        "DB_MIGRATION_PASSWORD": password,
        "DB_APP_USER": runtime,
        "DB_APP_PASSWORD": password,
        "SOURCE_WRITER_USER": writer,
        "SOURCE_WRITER_PASSWORD": password,
    }

    def bootstrap(*, overrides=None, expected_success=True) -> None:
        args = ["docker", "exec", "-i"]
        # All credentials are synthetic. Pass values via inherited env, not command arguments.
        for name in (
            "DB_MIGRATION_USER",
            "DB_MIGRATION_PASSWORD",
            "DB_APP_USER",
            "DB_APP_PASSWORD",
            "SOURCE_WRITER_USER",
            "SOURCE_WRITER_PASSWORD",
        ):
            args.extend(["-e", name])
        args.extend([container, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", config.DB_USER, "-d", database])
        result = subprocess.run(
            args,
            input=(ROOT / "infra/docker/postgres/configure-app-role.sql").read_text(),
            text=True,
            capture_output=True,
            env={**os.environ, **environment, **(overrides or {})},
            timeout=30,
        )
        assert (result.returncode == 0) is expected_success, "Unexpected synthetic bootstrap result"

    async def denied(engine, sql: str) -> None:
        with pytest.raises(DBAPIError) as error:
            async with engine.begin() as connection:
                await connection.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"

    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
        bootstrap(overrides={"SOURCE_WRITER_USER": config.DB_USER}, expected_success=False)
        bootstrap()
        async with admin.begin() as connection:
            await connection.execute(text(f'SET LOCAL ROLE "{owner}"'))
            for table in sorted(RUNTIME_MUTABLE_TABLES | RUNTIME_APPEND_ONLY_TABLES | set(SOURCE_TABLES)):
                await connection.execute(text(f'CREATE TABLE "{table}" (id integer PRIMARY KEY)'))
            await connection.execute(text("CREATE TABLE future_table (id serial PRIMARY KEY)"))
            await connection.execute(text('ALTER TABLE "user" ADD COLUMN sequence_id serial'))
            await connection.execute(
                text(f'GRANT ALL ON ALL TABLES IN SCHEMA public TO PUBLIC, "{runtime}", "{writer}"')
            )
            await connection.execute(text(f'GRANT UPDATE (id) ON checkin_audit TO PUBLIC, "{runtime}"'))
            await connection.execute(text(f'ALTER DEFAULT PRIVILEGES GRANT ALL ON TABLES TO "{runtime}"'))
            await connection.execute(
                text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "{runtime}"')
            )
        bootstrap()
        await run_provisioning(environment)
        for _ in range(2):
            # Bootstrap alone must not reopen the completed cutover.
            bootstrap()
            await denied(reader, "INSERT INTO rag_source_snapshot VALUES (3)")
            await denied(producer, "UPDATE rag_source_snapshot_verification SET id=2")
            await run_provisioning(environment)
        async with reader.begin() as connection:
            await connection.execute(text('INSERT INTO "user" (id) VALUES (1)'))
            await connection.execute(text('UPDATE "user" SET id=2'))
            await connection.execute(text("INSERT INTO checkin_audit VALUES (1)"))
            await connection.execute(text("INSERT INTO prescription_version VALUES (1)"))
        async with producer.begin() as connection:
            await connection.execute(text("INSERT INTO rag_source_snapshot VALUES (1)"))
            await connection.execute(text("UPDATE rag_source_snapshot SET id=2"))
            await connection.execute(text("INSERT INTO rag_source_snapshot_verification VALUES (1)"))
        async with admin.begin() as connection:
            await connection.execute(text(f'SET LOCAL ROLE "{owner}"'))
            await connection.execute(text("CREATE TABLE future_after_provision (id serial PRIMARY KEY)"))
        for engine, sql in [
            (reader, "UPDATE checkin_audit SET id=2"),
            (reader, "DELETE FROM checkin_audit"),
            (reader, "TRUNCATE checkin_audit"),
            (reader, "UPDATE prescription_version SET id=2"),
            (reader, "INSERT INTO rag_source_snapshot VALUES (3)"),
            (producer, "DELETE FROM rag_source_snapshot"),
            (producer, 'INSERT INTO "user" (id) VALUES (3)'),
            (reader, "INSERT INTO future_table VALUES (1)"),
            (reader, "INSERT INTO future_after_provision VALUES (1)"),
            (producer, "INSERT INTO future_after_provision VALUES (1)"),
            (reader, "SELECT nextval('future_after_provision_id_seq')"),
            (reader, "SELECT setval('user_sequence_id_seq', 100)"),
            (reader, f'SET ROLE "{writer}"'),
        ]:
            await denied(engine, sql)
        # A failed policy application must roll back its earlier revokes.
        async with admin.begin() as connection:
            await connection.execute(text("DROP TABLE checkin_audit"))
        with pytest.raises(ValueError, match="Required application tables"):
            await run_provisioning(environment)
        async with producer.begin() as connection:
            await connection.execute(text("INSERT INTO rag_source_snapshot VALUES (4)"))
        # Full applied migration history still contains the old transition function.
        # Provisioning must reject it, without requiring a newly defined test function.
        async with admin.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "head"],
            cwd=ROOT,
            env={**os.environ, "DB_NAME": database},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert migrated.returncode == 0, "Synthetic full-history migration failed"
        with pytest.raises(ValueError, match="legacy Source transition function"):
            await run_provisioning(environment)

    finally:
        await reader.dispose()
        await producer.dispose()
        await admin.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            for role in (writer, runtime, owner):
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await cluster.dispose()
