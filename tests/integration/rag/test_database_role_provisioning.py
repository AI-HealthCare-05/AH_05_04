"""Real psql bootstrap and separate credential provisioning in a disposable database."""

import os
import shutil
import subprocess
import sys
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.admin.source_writer import WriterConfig, run_selection
from app.core import config
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
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
            await connection.execute(
                text(
                    "ALTER TABLE rag_source_snapshot ADD COLUMN verification_status text, ADD COLUMN verified_at timestamptz, ADD COLUMN effective_at timestamptz"
                )
            )
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
            await denied(reader, "INSERT INTO rag_source_snapshot (id) VALUES (3)")
            await denied(producer, "UPDATE rag_source_snapshot_verification SET id=2")
            await run_provisioning(environment)
        async with reader.begin() as connection:
            await connection.execute(text('INSERT INTO "user" (id) VALUES (1)'))
            await connection.execute(text('UPDATE "user" SET id=2'))
            await connection.execute(text("INSERT INTO checkin_audit VALUES (1)"))
            await connection.execute(text("INSERT INTO prescription_version VALUES (1)"))
        async with producer.begin() as connection:
            await connection.execute(text("INSERT INTO rag_source_snapshot (id) VALUES (1)"))
            await connection.execute(text("UPDATE rag_source_snapshot SET verified_at=now()"))
            await connection.execute(text("INSERT INTO rag_source_snapshot_verification VALUES (1)"))
        async with admin.begin() as connection:
            await connection.execute(text(f'SET LOCAL ROLE "{owner}"'))
            await connection.execute(text("CREATE TABLE future_after_provision (id serial PRIMARY KEY)"))
        for engine, sql in [
            (reader, "UPDATE checkin_audit SET id=2"),
            (reader, "DELETE FROM checkin_audit"),
            (reader, "TRUNCATE checkin_audit"),
            (reader, "UPDATE prescription_version SET id=2"),
            (reader, "INSERT INTO rag_source_snapshot (id) VALUES (3)"),
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
            await connection.execute(text("INSERT INTO rag_source_snapshot (id) VALUES (4)"))
        # Full applied migration history still contains the old transition function.
        # Provisioning must reject it, without requiring a newly defined test function.
        async with admin.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "398b2c3d4e5f"],
            cwd=ROOT,
            env={**os.environ, "DB_NAME": database},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert migrated.returncode == 0, "Synthetic full-history migration failed"
        with pytest.raises(ValueError, match="legacy Source transition function"):
            await run_provisioning(environment)
        await _exercise_source_cutover(admin, reader, producer, environment, url, password)

    finally:
        await reader.dispose()
        await producer.dispose()
        await admin.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            for role in (writer, runtime, owner):
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await cluster.dispose()


async def _exercise_source_cutover(admin, reader, producer, environment, url, password):
    database = environment["DB_NAME"]
    runtime = environment["DB_APP_USER"]
    writer = environment["SOURCE_WRITER_USER"]
    sessions = async_sessionmaker(admin, expire_on_commit=False)
    async with sessions.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(RagSourceCreate(source_code="SYNTHETIC", display_name="Synthetic"))
        endpoint = await repository.create_endpoint(
            RagSourceEndpointCreate(source_id=source.id, endpoint_code="TEST", display_name="Synthetic")
        )
        operation = await repository.create_operation(
            RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="TEST", display_name="Synthetic")
        )
        snapshot = await repository.create_snapshot(
            RagSourceSnapshotCreate(
                operation_id=operation.id,
                source_version="synthetic:v1",
                raw_manifest_checksum="a" * 64,
                canonical_checksum="a" * 64,
                schema_version="1",
                parser_version="1",
                normalization_version="1",
                canonicalization_spec_version="1",
                record_count=0,
                rejected_record_count=0,
                collected_at=datetime.now(UTC),
            )
        )
    async with admin.begin() as connection:
        await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{runtime}", "{writer}"'))
        await connection.execute(text(f'GRANT ALL ON rag_source_snapshot TO PUBLIC, "{runtime}", "{writer}"'))
        await connection.execute(text(f'GRANT UPDATE (canonical_checksum) ON rag_source_snapshot TO "{runtime}"'))
    async with admin.begin() as connection:
        await connection.execute(
            text(
                "CREATE VIEW source_cutover_dependency AS SELECT transition_rag_source_snapshot("
                "NULL::text,NULL::text,NULL::text,NULL::timestamptz,NULL::timestamptz,NULL::text) AS allowed"
            )
        )
    failed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": database},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert failed.returncode != 0
    async with admin.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "398b2c3d4e5f"
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='rag_source_snapshot'::regclass AND NOT tgisinternal"
                )
            )
            == 3
        )
        assert await connection.scalar(
            text("SELECT has_column_privilege(:role, 'rag_source_snapshot', 'canonical_checksum', 'UPDATE')"),
            {"role": runtime},
        )
        await connection.execute(text("DROP VIEW source_cutover_dependency"))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": database},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, "Synthetic Source removal migration failed"
    # ACL removal is atomic with migration; provisioning has not run yet.
    for engine in (reader, producer):
        with pytest.raises(DBAPIError) as error:
            async with engine.begin() as connection:
                await connection.execute(text("UPDATE rag_source_snapshot SET verified_at=now()"))
        assert error.value.orig.sqlstate == "42501"
    async with admin.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM rag_source_snapshot")) == 1
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
                    "AND c.relname=ANY(:tables) AND NOT t.tgisinternal"
                ),
                {"tables": list(SOURCE_TABLES)},
            )
            == 0
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname IN ('transition_rag_source_snapshot', "
                    "'guard_rag_snapshot_state_write','prevent_rag_source_snapshot_mutation', "
                    "'prevent_rag_source_ingestion_artifact_mutation','prevent_rag_snapshot_verification_mutation')"
                )
            )
            == 0
        )
    await run_provisioning(environment)
    writer_config = WriterConfig(url.set(database=database, username=writer, password=password), "synthetic-operator")
    args = Namespace(snapshot_id=snapshot.id, expected_checksum="a" * 64, reason_code="SYNTHETIC_TEST")
    assert (await run_selection(writer_config, args)).decision.value == "ACTIVATED"
    assert (await run_selection(writer_config, args)).decision.value == "ALREADY_CURRENT"
    for engine, sql in [
        (reader, "UPDATE rag_source_snapshot SET verified_at=now()"),
        (producer, "UPDATE rag_source_snapshot SET canonical_checksum=repeat('b',64)"),
        (producer, "DELETE FROM rag_source_snapshot"),
        (producer, "UPDATE rag_source_snapshot_verification SET verified_by=NULL"),
        (producer, "DELETE FROM rag_source_ingestion_artifact"),
    ]:
        with pytest.raises(DBAPIError) as error:
            async with engine.begin() as connection:
                await connection.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"
    rollback = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "downgrade", "398b2c3d4e5f"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": database},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rollback.returncode != 0
    assert "Source trigger removal cannot be downgraded" in rollback.stderr
    async with admin.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "398c3d4e5f60"
        assert await connection.scalar(text("SELECT verification_status FROM rag_source_snapshot")) == "CURRENT"

    # A fresh database follows the same complete history to the trigger-free Source head.
    await reader.dispose()
    await producer.dispose()
    async with admin.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    fresh = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": database},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert fresh.returncode == 0, "Synthetic fresh database migration failed"
    await run_provisioning(environment)
    async with reader.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM rag_source_snapshot")) == 0
