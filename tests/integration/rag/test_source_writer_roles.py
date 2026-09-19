"""별도 로그인 credential로 Source Writer와 Runtime 경계를 확인합니다."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import config
from infra.python.source_role_policy import (
    SOURCE_TABLES,
    WRITER_LOCK_TABLES,
    apply_source_role_policy,
)


@pytest.mark.asyncio
async def test_separate_credentials_and_future_tables_are_fail_closed() -> None:
    suffix = uuid4().hex[:12]
    schema, runtime, writer = (f"source398_{part}_{suffix}" for part in ("schema", "runtime", "writer"))
    password = "synthetic-source398-test-only"
    url = URL.create(
        "postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_EXPOSE_PORT,
        database=config.DB_NAME,
    )
    admin = create_async_engine(url)
    reader = create_async_engine(url.set(username=runtime, password=password))
    producer = create_async_engine(url.set(username=writer, password=password))
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for role in (runtime, writer):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            for table in SOURCE_TABLES:
                await connection.execute(text(f'CREATE TABLE "{schema}"."{table}" (id integer PRIMARY KEY)'))
            await connection.execute(
                text(
                    f'CREATE TABLE "{schema}".rag_source_use_approval ('
                    "id integer PRIMARY KEY, source_snapshot_id integer, source_code text, source_version text, "
                    "environment text, purpose text, approval_version text, valid_from timestamptz, "
                    "expires_at timestamptz, revoked_at timestamptz, revoked_by integer, revoked_reason text, "
                    "actor_id integer, evidence_ref text)"
                )
            )
            await connection.execute(
                text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" GRANT ALL ON TABLES TO "{runtime}", "{writer}"')
            )
            await connection.execute(text(f'GRANT "{writer}" TO "{runtime}"'))
            with pytest.raises(ValueError, match="inherit or SET ROLE"):
                await apply_source_role_policy(
                    connection, schema=schema, owner=config.DB_USER, runtime=runtime, writer=writer
                )
            await connection.execute(text(f'REVOKE "{writer}" FROM "{runtime}"'))
            await connection.execute(text(f'ALTER DEFAULT PRIVILEGES GRANT INSERT ON TABLES TO "{writer}"'))
            with pytest.raises(ValueError, match="global table default grants"):
                await apply_source_role_policy(
                    connection, schema=schema, owner=config.DB_USER, runtime=runtime, writer=writer
                )
            await connection.execute(text(f'ALTER DEFAULT PRIVILEGES REVOKE INSERT ON TABLES FROM "{writer}"'))
            await connection.execute(
                text(
                    f'ALTER TABLE "{schema}".rag_source_ingestion_run ADD COLUMN snapshot_id integer, ADD COLUMN run_status text, ADD COLUMN failure_code text, ADD COLUMN failure_message text, ADD COLUMN duration_ms integer, ADD COLUMN finished_at timestamptz, ADD COLUMN attempted_source_version text'
                )
            )
            await connection.execute(
                text(
                    f'ALTER TABLE "{schema}".rag_source_snapshot ADD COLUMN verification_status text, ADD COLUMN verified_at timestamptz, ADD COLUMN effective_at timestamptz, ADD COLUMN verification_seal_id char(36)'
                )
            )
            for table in WRITER_LOCK_TABLES:
                await connection.execute(
                    text(
                        f'ALTER TABLE "{schema}"."{table}" ADD COLUMN knowledge_index_lock_marker integer NOT NULL DEFAULT 0 CHECK (knowledge_index_lock_marker=0)'
                    )
                )
            await connection.execute(text(f'CREATE TABLE "{schema}".unrelated (id integer)'))
            await connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{runtime}", "{writer}"'))
            await connection.execute(text(f'GRANT INSERT (id), UPDATE (id) ON "{schema}".unrelated TO "{writer}"'))
            for table in SOURCE_TABLES:
                await connection.execute(
                    text(
                        f'GRANT INSERT (id), UPDATE (id), REFERENCES (id) ON "{schema}"."{table}" '
                        f'TO PUBLIC, "{runtime}", "{writer}"'
                    )
                )
            # These are independent grants, despite the absence of table-level UPDATE.
            assert await connection.scalar(
                text(f"SELECT has_column_privilege('{runtime}', '{schema}.rag_source_snapshot', 'id', 'UPDATE')")
            )
            await connection.execute(text(f'ALTER ROLE "{writer}" REPLICATION'))
            with pytest.raises(ValueError, match="administrative privileges"):
                await apply_source_role_policy(
                    connection, schema=schema, owner=config.DB_USER, runtime=runtime, writer=writer
                )
            await connection.execute(text(f'ALTER ROLE "{writer}" NOREPLICATION'))
            await _assert_invalid_markers_rejected(connection, schema, runtime, writer)
            for _ in range(2):
                await apply_source_role_policy(
                    connection, schema=schema, owner=config.DB_USER, runtime=runtime, writer=writer
                )
            for role in (runtime, writer):
                assert not await connection.scalar(
                    text(
                        f"SELECT has_column_privilege('{role}', "
                        f"'{schema}.rag_source_snapshot_verification', 'id', 'REFERENCES')"
                    )
                )
            await connection.execute(text(f'CREATE TABLE "{schema}".future_table (id integer)'))
        async with producer.begin() as connection:
            await connection.execute(text(f'INSERT INTO "{schema}".rag_source_snapshot (id) VALUES (1)'))
            await connection.execute(text(f'INSERT INTO "{schema}".rag_source_use_approval (id) VALUES (1)'))
            await connection.execute(
                text(f'UPDATE "{schema}".rag_source_use_approval SET revoked_reason=:reason WHERE id=1'),
                {"reason": "policy change"},
            )
            await connection.execute(text(f'UPDATE "{schema}".rag_source_snapshot SET verified_at=now() WHERE id=1'))
            await connection.execute(text(f'INSERT INTO "{schema}".rag_source_snapshot_verification VALUES (1)'))
            await connection.execute(text(f'INSERT INTO "{schema}".rag_source_ingestion_run (id) VALUES (1)'))
            await connection.execute(
                text(f'UPDATE "{schema}".rag_source_ingestion_run SET run_status=:status'), {"status": "FAILED"}
            )
        await _assert_writer_lock_boundaries(producer, schema)
        async with reader.connect() as connection:
            assert await connection.scalar(text(f'SELECT id FROM "{schema}".rag_source_snapshot')) == 1
            assert await connection.scalar(text(f'SELECT id FROM "{schema}".rag_source_use_approval')) == 1
        denied = [
            (producer, f'UPDATE "{schema}".rag_source_ingestion_run SET attempted_source_version=NULL'),
            (reader, f'INSERT INTO "{schema}".rag_source_use_approval (id) VALUES (2)'),
            (reader, f'UPDATE "{schema}".rag_source_use_approval SET id=2'),
            (reader, f'UPDATE "{schema}".rag_source_use_approval SET revoked_reason=NULL'),
            (reader, f'DELETE FROM "{schema}".rag_source_use_approval'),
            (producer, f'UPDATE "{schema}".rag_source_use_approval SET id=2'),
            (producer, f"UPDATE \"{schema}\".rag_source_use_approval SET source_code='CHANGED'"),
            (producer, f"UPDATE \"{schema}\".rag_source_use_approval SET approval_version='approval-2'"),
            (producer, f'UPDATE "{schema}".rag_source_use_approval SET valid_from=now()'),
            (producer, f'DELETE FROM "{schema}".rag_source_use_approval'),
            (reader, f'UPDATE "{schema}".rag_source_ingestion_run SET run_status=NULL'),
            (reader, f'INSERT INTO "{schema}".rag_source_snapshot (id) VALUES (3)'),
            (reader, f'UPDATE "{schema}".rag_source_snapshot SET id=3'),
            (reader, f'DELETE FROM "{schema}".rag_source_snapshot'),
            (reader, f'SET ROLE "{writer}"'),
            (reader, f'CREATE TABLE "{schema}".unauthorized (id integer)'),
            (producer, f'UPDATE "{schema}".rag_source_snapshot_verification SET id=2'),
            (producer, f'DELETE FROM "{schema}".rag_source_snapshot_verification'),
            (producer, f'TRUNCATE "{schema}".rag_source_snapshot_verification'),
            (reader, f'INSERT INTO "{schema}".future_table VALUES (1)'),
            (producer, f'INSERT INTO "{schema}".future_table VALUES (1)'),
            (producer, f'INSERT INTO "{schema}".unrelated (id) VALUES (1)'),
            (producer, f'UPDATE "{schema}".unrelated SET id=2'),
        ]
        for engine, sql in denied:
            with pytest.raises(DBAPIError) as error:
                async with engine.begin() as connection:
                    await connection.execute(text(sql))
            assert error.value.orig.sqlstate == "42501"
    finally:
        await reader.dispose()
        await producer.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            for role in (runtime, writer):
                exists = await connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role})
                if exists:
                    await connection.execute(text(f'DROP OWNED BY "{role}"'))
                    await connection.execute(text(f'DROP ROLE "{role}"'))
        await admin.dispose()


async def _assert_writer_lock_boundaries(producer, schema):
    for table in WRITER_LOCK_TABLES:
        async with producer.begin() as connection:
            await connection.execute(text(f'INSERT INTO "{schema}"."{table}" (id) VALUES (1)'))
            await connection.execute(text(f'SELECT id FROM "{schema}"."{table}" FOR UPDATE'))
        for statement, sqlstate in (
            (f'UPDATE "{schema}"."{table}" SET id=2', "42501"),
            (f'DELETE FROM "{schema}"."{table}"', "42501"),
            (f'UPDATE "{schema}"."{table}" SET knowledge_index_lock_marker=1', "23514"),
        ):
            with pytest.raises(DBAPIError) as error:
                async with producer.begin() as connection:
                    await connection.execute(text(statement))
            assert error.value.orig.sqlstate == sqlstate


async def _assert_invalid_markers_rejected(connection, schema, runtime, writer):
    target = f'"{schema}".rag_source'
    for alteration in (
        "ALTER COLUMN knowledge_index_lock_marker DROP NOT NULL",
        "DROP COLUMN knowledge_index_lock_marker",
        "DROP CONSTRAINT rag_source_knowledge_index_lock_marker_check",
        "DROP CONSTRAINT rag_source_knowledge_index_lock_marker_check, "
        "ADD CONSTRAINT marker_not_valid CHECK (knowledge_index_lock_marker=0) NOT VALID",
        "DROP CONSTRAINT rag_source_knowledge_index_lock_marker_check, "
        "ADD CONSTRAINT marker_wrong_value CHECK (knowledge_index_lock_marker=1)",
    ):
        transaction = await connection.begin_nested()
        try:
            await connection.execute(text(f"ALTER TABLE {target} {alteration}"))
            with pytest.raises(ValueError, match="validated CHECK equal to zero"):
                await apply_source_role_policy(
                    connection, schema=schema, owner=config.DB_USER, runtime=runtime, writer=writer
                )
            assert await connection.scalar(
                text(f"SELECT has_column_privilege('{runtime}', '{schema}.rag_source', 'id', 'UPDATE')")
            )
        finally:
            await transaction.rollback()
