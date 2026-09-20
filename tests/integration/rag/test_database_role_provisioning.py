"""Real psql bootstrap and separate credential provisioning in a disposable database."""

import asyncio
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.admin.source_writer import WriterConfig, run_selection
from app.core import config
from app.models.rag_runtime import RagRuntimeEnvironmentTransitionKind
from app.repositories.rag_runtime_repository import (
    RagRuntimeEnvironmentTransitionCreate,
    RagRuntimeRepository,
    RuntimeEnvironmentTransitionConflictError,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
)
from app.services.rag_runtime import RagRuntimeEnvironmentTransitionService
from infra.python.catalog_role_policy import CATALOG_APPROVAL_READ_TABLES, CATALOG_WRITE_TABLES
from infra.python.knowledge_index_role_policy import KNOWLEDGE_INDEX_RUNTIME_READ_TABLES
from infra.python.provision_database_roles import (
    ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES,
    ACCOUNT_WITHDRAWAL_CLEANUP_INSERT_TABLES,
    ACCOUNT_WITHDRAWAL_CLEANUP_READ_TABLES,
    ACCOUNT_WITHDRAWAL_CLEANUP_UPDATE_COLUMNS,
    ACCOUNT_WITHDRAWAL_RUNTIME_PROTECTED_DELETE_TABLES,
    CANDIDATE_INDEX_RUNTIME_READ_TABLES,
    RUNTIME_ACCOUNT_DELETION_REQUEST_UPDATE_COLUMNS,
    RUNTIME_APPEND_ONLY_TABLES,
    RUNTIME_AUTH_UPDATE_COLUMNS,
    RUNTIME_CHECKIN_LOCK_TABLES,
    RUNTIME_LIFESTYLE_TABLES,
    RUNTIME_MUTABLE_TABLES,
    RUNTIME_RETRIEVAL_RUN_TABLES,
    RUNTIME_TRACK_C_APPEND_TABLES,
    RUNTIME_TRACK_C_FOLLOWUP_INSERT_ONLY_TABLES,
    RUNTIME_TRACK_C_FOLLOWUP_TABLES,
    RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS,
    run_provisioning,
)
from infra.python.source_management_role_policy import CATALOG_TABLES
from infra.python.source_role_policy import (
    SOURCE_TABLES,
    SOURCE_USE_APPROVAL_TABLES,
    WRITER_LOCK_TABLES,
    quoted_identifier,
)

ROOT = Path(__file__).resolve().parents[3]

# Runtime append-only 표에 기대하는 권한. 이력은 남기되 고쳐 쓰지 않는다.
_APPEND_ONLY_PRIVILEGES = {
    "SELECT": True,
    "INSERT": True,
    "UPDATE": False,
    "DELETE": False,
    "TRUNCATE": False,
}
_READ_ONLY_PRIVILEGES = {
    "SELECT": True,
    "INSERT": False,
    "UPDATE": False,
    "DELETE": False,
    "TRUNCATE": False,
}
# #178/#689: retrieval_run lifecycle needs UPDATE; #748 cleanup DELETE is isolated to a dedicated role.
_RETRIEVAL_RUN_PRIVILEGES = {**_APPEND_ONLY_PRIVILEGES, "UPDATE": True}
# #820: 정정 이력 audit은 런타임이 읽지 않는다. INSERT만 열린다.
_INSERT_ONLY_PRIVILEGES = {**_APPEND_ONLY_PRIVILEGES, "SELECT": False}


async def _assert_runtime_table_privileges(admin, runtime: str, expected: dict[str, dict[str, bool]]) -> None:
    """Runtime role의 실제 PostgreSQL table 권한이 기대한 집합과 정확히 같은지 확인한다."""
    async with admin.connect() as connection:
        for table, privileges in expected.items():
            observed = {
                privilege: await connection.scalar(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {"role": runtime, "table": table, "privilege": privilege},
                )
                for privilege in privileges
            }
            assert observed == privileges, table


async def _assert_account_withdrawal_cleanup_delete_privileges(connection, role: str) -> None:
    for table in sorted(ACCOUNT_WITHDRAWAL_CLEANUP_READ_TABLES):
        assert await connection.scalar(
            text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
            {"role": role, "table": table},
        ), table
        assert not await connection.scalar(
            text("SELECT has_table_privilege(:role, :table, 'TRUNCATE')"),
            {"role": role, "table": table},
        ), table
    for table in sorted(ACCOUNT_WITHDRAWAL_CLEANUP_DELETE_TABLES):
        assert await connection.scalar(
            text("SELECT has_table_privilege(:role, :table, 'DELETE')"),
            {"role": role, "table": table},
        ), table
    for table in sorted(ACCOUNT_WITHDRAWAL_CLEANUP_INSERT_TABLES):
        assert await connection.scalar(
            text("SELECT has_table_privilege(:role, :table, 'INSERT')"),
            {"role": role, "table": table},
        ), table
    for table, columns in ACCOUNT_WITHDRAWAL_CLEANUP_UPDATE_COLUMNS.items():
        for column in columns:
            assert await connection.scalar(
                text("SELECT has_column_privilege(:role, :table, :column, 'UPDATE')"),
                {"role": role, "table": table, "column": column},
            ), (table, column)


async def _exercise_track_c_runtime_writes(connection) -> None:
    """#820: Track C 새 revision INSERT. GRANT INSERT가 빠지면 여기서 42501로 걸린다."""
    for table in sorted(RUNTIME_TRACK_C_APPEND_TABLES | RUNTIME_TRACK_C_FOLLOWUP_TABLES):
        await connection.execute(text(f"INSERT INTO {quoted_identifier(table)} (id) VALUES (1)"))
    # follow-up 현재 응답 갱신은 부여한 세 컬럼으로만 가능하다.
    await connection.execute(text("UPDATE action_plan_followup SET response='HELPED', revision=2, updated_at=now()"))


async def _assert_track_c_runtime_column_privileges(connection, runtime: str) -> None:
    """#820: follow-up은 갱신 대상 컬럼만 UPDATE 가능하고 audit은 어떤 컬럼도 열리지 않는다."""
    followup_columns = ("id", *RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS)
    observed = {
        column: await connection.scalar(
            text("SELECT has_column_privilege(:role, 'action_plan_followup', :column, 'UPDATE')"),
            {"role": runtime, "column": column},
        )
        for column in followup_columns
    }
    assert observed == {column: column in RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS for column in followup_columns}
    assert not await connection.scalar(
        text("SELECT has_column_privilege(:role, 'action_plan_followup_audit', 'id', 'UPDATE')"),
        {"role": runtime},
    )


async def _assert_runtime_cannot_delete_withdrawal_cleanup_tables(connection, runtime: str) -> None:
    for table in sorted(ACCOUNT_WITHDRAWAL_RUNTIME_PROTECTED_DELETE_TABLES):
        assert not await connection.scalar(
            text("SELECT has_table_privilege(:role, :table, 'DELETE')"),
            {"role": runtime, "table": table},
        ), table
        assert not await connection.scalar(
            text("SELECT has_table_privilege(:role, :table, 'TRUNCATE')"),
            {"role": runtime, "table": table},
        ), table


async def _assert_permission_denied(engine, sql: str) -> None:
    with pytest.raises(DBAPIError) as error:
        async with engine.begin() as connection:
            await connection.execute(text(sql))
    assert error.value.orig.sqlstate == "42501"


async def _assert_runtime_delete_policy(reader, table: str) -> None:
    await _assert_permission_denied(reader, f"DELETE FROM {table} WHERE false")


async def _create_role_fixture_table(connection, table: str) -> None:
    if table == "rag_source_use_approval":
        await connection.execute(
            text(
                'CREATE TABLE "rag_source_use_approval" ('
                "id integer PRIMARY KEY, revoked_at timestamptz, revoked_by integer, revoked_reason text)"
            )
        )
        return
    await connection.execute(text(f'CREATE TABLE "{table}" (id integer PRIMARY KEY)'))


async def test_bootstrap_then_provision_and_redeploy_do_not_reopen_permissions() -> None:
    container = os.environ.get("ISSUE398_TEST_POSTGRES_CONTAINER")
    if not container or not shutil.which("docker"):
        pytest.skip("Requires an explicitly selected disposable PostgreSQL container")
    suffix = uuid4().hex[:12]
    database = f"provision398_{suffix}"
    owner, runtime, writer, cleanup = (
        f"provision398_{part}_{suffix}" for part in ("owner", "runtime", "writer", "cleanup")
    )
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
    cleanup_reader = create_async_engine(url.set(database=database, username=cleanup, password=password))
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
        "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE": cleanup,
        "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD": password,
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
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE",
            "ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD",
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
            for table in sorted(
                RUNTIME_MUTABLE_TABLES
                | RUNTIME_APPEND_ONLY_TABLES
                | RUNTIME_LIFESTYLE_TABLES
                | RUNTIME_CHECKIN_LOCK_TABLES
                | RUNTIME_TRACK_C_APPEND_TABLES
                | RUNTIME_TRACK_C_FOLLOWUP_TABLES
                | {"support_action_plan"}
                | RUNTIME_RETRIEVAL_RUN_TABLES
                | CATALOG_WRITE_TABLES
                | CATALOG_APPROVAL_READ_TABLES
                | KNOWLEDGE_INDEX_RUNTIME_READ_TABLES
                | CANDIDATE_INDEX_RUNTIME_READ_TABLES
                | set(SOURCE_TABLES)
                | set(SOURCE_USE_APPROVAL_TABLES)
                | set(RUNTIME_AUTH_UPDATE_COLUMNS)
                | ACCOUNT_WITHDRAWAL_CLEANUP_READ_TABLES
                | {"account_deletion_request", "notification_record", "user_consent"}
            ):
                await _create_role_fixture_table(connection, table)
            await _add_checkin_lock_fixture_columns(connection)
            await _add_track_c_followup_fixture_columns(connection)
            await _add_account_deletion_request_fixture_columns(connection)
            await _add_account_withdrawal_cleanup_fixture_columns(connection)
            await connection.execute(
                text(
                    "ALTER TABLE rag_source_ingestion_run ADD COLUMN snapshot_id integer, ADD COLUMN run_status text, ADD COLUMN failure_code text, ADD COLUMN failure_message text, ADD COLUMN duration_ms integer, ADD COLUMN finished_at timestamptz, ADD COLUMN attempted_source_version text"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE rag_source_snapshot ADD COLUMN verification_status text, ADD COLUMN verified_at timestamptz, ADD COLUMN effective_at timestamptz, ADD COLUMN verification_seal_id char(36)"
                )
            )
            for table in WRITER_LOCK_TABLES:
                await connection.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN knowledge_index_lock_marker integer NOT NULL DEFAULT 0 CHECK (knowledge_index_lock_marker=0)"
                    )
                )
            await connection.execute(
                text(
                    "ALTER TABLE rag_candidate_index_version "
                    "ADD COLUMN candidate_index_lock_marker integer NOT NULL DEFAULT 0 "
                    "CHECK (candidate_index_lock_marker=0)"
                )
            )
            await _add_auth_fixture_columns(connection)
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
            await connection.execute(text(f'GRANT UPDATE (id) ON medication_schedule_audit TO PUBLIC, "{runtime}"'))
            await connection.execute(
                text(f'GRANT DELETE, TRUNCATE ON medication_schedule_audit TO PUBLIC, "{runtime}"')
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
            await connection.execute(text("INSERT INTO medication_schedule_audit VALUES (1)"))
            await connection.execute(text("INSERT INTO prescription_version VALUES (1)"))
            await connection.execute(text("INSERT INTO account_deletion_request (id) VALUES (1)"))
            await connection.execute(text("SELECT * FROM account_deletion_request FOR UPDATE"))
            await connection.execute(text("UPDATE account_deletion_request SET status='IN_PROGRESS'"))
            await connection.execute(text("INSERT INTO push_subscription VALUES (1)"))
            await connection.execute(text("INSERT INTO push_delivery VALUES (1)"))
            await connection.execute(text("INSERT INTO lifestyle_times VALUES (1)"))
            await connection.execute(text("UPDATE push_subscription SET id=2"))
            await connection.execute(text("UPDATE lifestyle_times SET id=2"))
            await connection.execute(text("DELETE FROM push_delivery"))
            await _exercise_track_c_runtime_writes(connection)
        async with producer.begin() as connection:
            await connection.execute(text("INSERT INTO rag_source_snapshot (id) VALUES (1)"))
            await connection.execute(text("INSERT INTO rag_source_use_approval (id) VALUES (1)"))
            await connection.execute(text("UPDATE rag_source_use_approval SET revoked_reason='policy change'"))
            await connection.execute(text("UPDATE rag_source_snapshot SET verified_at=now()"))
            await connection.execute(text("INSERT INTO rag_source_snapshot_verification VALUES (1)"))
        async with admin.begin() as connection:
            await connection.execute(text(f'SET LOCAL ROLE "{owner}"'))
            await connection.execute(text("CREATE TABLE future_after_provision (id serial PRIMARY KEY)"))
        for engine, sql in [
            (producer, "SELECT * FROM push_subscription"),
            (producer, "INSERT INTO push_delivery VALUES (2)"),
            (reader, "TRUNCATE push_subscription"),
            (reader, "TRUNCATE push_delivery"),
            (reader, "DELETE FROM lifestyle_times"),
            (reader, "TRUNCATE lifestyle_times"),
            (reader, "UPDATE medication_schedule_audit SET id=2"),
            (reader, "DELETE FROM medication_schedule_audit"),
            (reader, "TRUNCATE medication_schedule_audit"),
            (producer, "SELECT * FROM medication_schedule_audit"),
            (producer, "INSERT INTO medication_schedule_audit VALUES (2)"),
            (producer, "UPDATE rag_source_ingestion_run SET attempted_source_version=NULL"),
            (reader, "UPDATE rag_source_ingestion_run SET run_status=NULL"),
            (reader, "UPDATE checkin_audit SET id=2"),
            (reader, "DELETE FROM checkin_audit"),
            (reader, "UPDATE rag_medication_product SET id=2"),
            (reader, "DELETE FROM rag_medication_alias"),
            (reader, "TRUNCATE checkin_audit"),
            (reader, "UPDATE prescription_version SET id=2"),
            (reader, "UPDATE account_deletion_request SET id=2"),
            (reader, "UPDATE account_deletion_request SET user_id='00000000-0000-0000-0000-000000000002'"),
            (reader, "UPDATE account_deletion_request SET requested_at=now()"),
            (reader, "UPDATE account_deletion_request SET created_at=now()"),
            (reader, "DELETE FROM account_deletion_request"),
            (reader, "TRUNCATE account_deletion_request"),
            (reader, "INSERT INTO rag_source_snapshot (id) VALUES (3)"),
            (producer, "DELETE FROM rag_source_snapshot"),
            (reader, "INSERT INTO rag_source_use_approval (id) VALUES (2)"),
            (reader, "UPDATE rag_source_use_approval SET id=2"),
            (reader, "UPDATE rag_source_use_approval SET revoked_reason=NULL"),
            (reader, "DELETE FROM rag_source_use_approval"),
            (producer, "UPDATE rag_source_use_approval SET id=2"),
            (producer, "DELETE FROM rag_source_use_approval"),
            (producer, 'INSERT INTO "user" (id) VALUES (3)'),
            (reader, "INSERT INTO rag_candidate_index_version VALUES (1)"),
            (reader, "DELETE FROM rag_candidate_index_version"),
            (reader, "TRUNCATE rag_candidate_index_version"),
            (reader, "INSERT INTO rag_candidate_index_member VALUES (1)"),
            (reader, "UPDATE rag_candidate_index_member SET id=2"),
            (reader, "DELETE FROM rag_candidate_index_member"),
            (reader, "TRUNCATE rag_candidate_index_member"),
            (reader, "INSERT INTO future_table VALUES (1)"),
            (reader, "INSERT INTO future_after_provision VALUES (1)"),
            (producer, "INSERT INTO future_after_provision VALUES (1)"),
            (reader, "SELECT nextval('future_after_provision_id_seq')"),
            (reader, "SELECT setval('user_sequence_id_seq', 100)"),
            # #820: Track C 이력 삭제·전체 컬럼 UPDATE는 Runtime에 열리지 않는다.
            (reader, "DELETE FROM safety_assessment"),
            (reader, "TRUNCATE safety_assessment"),
            (reader, "UPDATE safety_assessment SET id=2"),
            (producer, "INSERT INTO safety_assessment (id) VALUES (2)"),
            (reader, "DELETE FROM barrier_response"),
            (reader, "TRUNCATE barrier_response"),
            (reader, "UPDATE barrier_response SET id=2"),
            (producer, "INSERT INTO barrier_response (id) VALUES (2)"),
            (reader, "DELETE FROM support_action_plan"),
            (reader, "TRUNCATE support_action_plan"),
            (reader, "UPDATE support_action_plan SET id=2"),
            (producer, "INSERT INTO support_action_plan (id) VALUES (2)"),
            (reader, "DELETE FROM action_plan_followup"),
            (reader, "TRUNCATE action_plan_followup"),
            (reader, "UPDATE action_plan_followup SET id=2"),
            (producer, "INSERT INTO action_plan_followup (id) VALUES (2)"),
            (reader, "DELETE FROM action_plan_followup_audit"),
            (reader, "TRUNCATE action_plan_followup_audit"),
            (reader, "UPDATE action_plan_followup_audit SET id=2"),
            (producer, "INSERT INTO action_plan_followup_audit (id) VALUES (2)"),
            (reader, "SELECT * FROM action_plan_followup_audit"),
            (reader, f'SET ROLE "{writer}"'),
        ]:
            await denied(engine, sql)
        # #731: REQUEST authority 증거는 Runtime append-only 정책을 그대로 따른다.
        # 읽기(#709 Production Reader)와 발행(#713 writer)만 허용하고 이력 변경은 막는다.
        await _assert_runtime_table_privileges(
            admin,
            runtime,
            {
                "rag_request_guard_authority": _APPEND_ONLY_PRIVILEGES,
                "rag_request_source_decision": _APPEND_ONLY_PRIVILEGES,
                "rag_request_member_decision": _APPEND_ONLY_PRIVILEGES,
                "rag_request_guard_runtime_binding": _APPEND_ONLY_PRIVILEGES,
                "rag_source_use_approval": _READ_ONLY_PRIVILEGES,
                # #780: Candidate Index tables are runtime read-only
                "rag_candidate_index_version": _READ_ONLY_PRIVILEGES,
                "rag_candidate_index_member": _READ_ONLY_PRIVILEGES,
                # #820: Track C 쓰기 경로는 INSERT만 열고 UPDATE/DELETE/TRUNCATE는 테이블 단위로 닫혀 있다.
                # 정정 이력 audit은 읽는 코드가 없어 SELECT도 주지 않는다.
                **{
                    table: (
                        _INSERT_ONLY_PRIVILEGES
                        if table in RUNTIME_TRACK_C_FOLLOWUP_INSERT_ONLY_TABLES
                        else _APPEND_ONLY_PRIVILEGES
                    )
                    for table in sorted(RUNTIME_TRACK_C_APPEND_TABLES | RUNTIME_TRACK_C_FOLLOWUP_TABLES)
                },
                # 대조군: 기존 lifecycle/append-only 권한이 바뀌지 않았는지 확인한다.
                "retrieval_run": _RETRIEVAL_RUN_PRIVILEGES,
                "ai_job_intake_context": _APPEND_ONLY_PRIVILEGES,
            },
        )
        async with admin.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT has_column_privilege(:role, 'rag_candidate_index_version', 'candidate_index_lock_marker', 'UPDATE')"
                    ),
                    {"role": runtime},
                )
                is True
            )
            account_deletion_request_columns = (
                "id",
                "user_id",
                "status",
                "requested_at",
                "started_at",
                "completed_at",
                "failed_at",
                "retry_count",
                "last_error_code",
                "created_at",
                "updated_at",
            )
            observed = {
                column: await connection.scalar(
                    text("SELECT has_column_privilege(:role, 'account_deletion_request', :column, 'UPDATE')"),
                    {"role": runtime, "column": column},
                )
                for column in account_deletion_request_columns
            }
            assert observed == {
                column: column in RUNTIME_ACCOUNT_DELETION_REQUEST_UPDATE_COLUMNS
                for column in account_deletion_request_columns
            }
            await _assert_track_c_runtime_column_privileges(connection, runtime)
            await _assert_runtime_cannot_delete_withdrawal_cleanup_tables(connection, runtime)
            await _assert_account_withdrawal_cleanup_delete_privileges(connection, cleanup)
        # #731: authority 표가 빠진 schema에서는 provisioning이 fail closed여야 한다.
        async with admin.begin() as connection:
            await connection.execute(text("DROP TABLE rag_request_member_decision"))
        with pytest.raises(ValueError, match="Required application tables"):
            await run_provisioning(environment)
        async with admin.begin() as connection:
            await connection.execute(text(f'SET LOCAL ROLE "{owner}"'))
            await connection.execute(text("CREATE TABLE rag_request_member_decision (id integer PRIMARY KEY)"))
        await run_provisioning(environment)
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
        await cleanup_reader.dispose()
        await admin.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            for role in (cleanup, writer, runtime, owner):
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await cluster.dispose()


async def _exercise_source_cutover(admin, reader, producer, environment, url, password):
    database = environment["DB_NAME"]
    runtime = environment["DB_APP_USER"]
    writer = environment["SOURCE_WRITER_USER"]
    # The historical 398c schema predates #178, while this test intentionally uses today's
    # endpoint/operation ORM mappers. Add only their server-generated compatibility columns
    # for the ORM flush, then remove them before Alembic advances to the real #178 revision.
    async with admin.begin() as connection:
        for table in ("rag_source_endpoint", "rag_source_operation"):
            await connection.execute(
                text(f"ALTER TABLE {table} ADD COLUMN knowledge_index_lock_marker integer DEFAULT 0 NOT NULL")
            )
    sessions = async_sessionmaker(admin, expire_on_commit=False)
    async with sessions.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source_id = uuid4()
        await session.execute(
            text(
                "INSERT INTO rag_source (id,source_code,display_name,lifecycle_status) "
                "VALUES (:id,'SYNTHETIC','Synthetic','DRAFT')"
            ),
            {"id": str(source_id)},
        )
        endpoint = await repository.create_endpoint(
            RagSourceEndpointCreate(source_id=source_id, endpoint_code="TEST", display_name="Synthetic")
        )
        operation = await repository.create_operation(
            RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="TEST", display_name="Synthetic")
        )
        snapshot_id = uuid4()
        # Historical revision fixture uses historical columns, not today's ORM model.
        await session.execute(
            text(
                "INSERT INTO rag_source_snapshot (id,operation_id,source_version,raw_manifest_checksum,canonical_checksum,"
                "schema_version,parser_version,normalization_version,canonicalization_spec_version,"
                "record_count,rejected_record_count,verification_status,collected_at,endpoint_receipt_hash) "
                "VALUES (:id,:operation,'api:2026-09-10T00:00:00.000000Z:' || repeat('a',64),"
                "repeat('a',64),repeat('a',64),'1','1','1','1',0,0,'PENDING',now(),repeat('d',64))"
            ),
            {"id": str(snapshot_id), "operation": str(operation.id)},
        )
    async with admin.begin() as connection:
        for table in ("rag_source_endpoint", "rag_source_operation"):
            await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN knowledge_index_lock_marker"))
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
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "398c3d4e5f60"],
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
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "398c3d4e5f60"],
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
    await _grant_historical_test_permissions(admin, environment)
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
        assert await connection.scalar(text("SELECT verification_status FROM rag_source_snapshot")) == "PENDING"

    runtime_environment_id = await _exercise_audit_cutover(admin, reader, producer, environment)

    # 과거 398 revision을 재현하면서 현재 ORM helper가 추가로 쓰는 후속 컬럼만 잠시 제공한다.
    # 실제 head upgrade 전에 제거해 #458 migration이 컬럼을 직접 생성하도록 한다.
    async with admin.begin() as connection:
        await connection.execute(text("ALTER TABLE ocr_job ADD COLUMN llm_processing varchar(32)"))
    await _exercise_prescription_candidate_cutover(admin, reader, environment)
    async with admin.begin() as connection:
        await connection.execute(text("ALTER TABLE ocr_job DROP COLUMN llm_processing"))
    await _assert_runtime_transition_revisions_are_sealed(admin, runtime_environment_id, environment)

    current = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": database},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert current.returncode == 0, "Synthetic current-head migration failed"
    await run_provisioning(environment)
    await _exercise_preflight_context_runtime_permissions(reader, producer)
    await _exercise_notification_runtime_permissions(reader, producer)
    await _exercise_feedback_runtime_permissions(reader, producer)
    for with_history in (False, True):
        await _exercise_checkin_correction_runtime_permissions(admin, reader, producer, with_history=with_history)
    await _exercise_retrieval_run_runtime_permissions(reader, producer, admin)
    writer_config = WriterConfig(url.set(database=database, username=writer, password=password), "synthetic-operator")
    args = Namespace(snapshot_id=snapshot_id, expected_checksum="a" * 64, reason_code="SYNTHETIC_TEST")
    assert (await run_selection(writer_config, args)).decision.value == "ACTIVATED"
    assert (await run_selection(writer_config, args)).decision.value == "ALREADY_CURRENT"

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
    await _exercise_retrieval_run_runtime_permissions(reader, producer, admin)


async def _exercise_audit_cutover(admin, reader, producer, environment):
    tables = (
        "rag_runtime_environment_transition",
        "checkin_audit",
        "rag_citation",
        "rag_evidence_guideline",
        "rag_evidence_rule",
        "rag_evidence",
        "rag_evidence_knowledge",
    )
    runtime = environment["DB_APP_USER"]
    env_id, transition_id = str(uuid4()), str(uuid4())
    async with admin.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_runtime_environment (id,environment_code,environment_status) "
                "VALUES (:id,'TEST','ACTIVE')"
            ),
            {"id": env_id},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_runtime_environment_transition "
                "(id,environment_id,transition_kind,environment_revision,safety_epoch,guard_decision_ref) "
                "VALUES (:id,:environment,'SUSPEND',1,1,'synthetic-guard')"
            ),
            {"id": transition_id, "environment": env_id},
        )
        for table in tables:
            await connection.execute(text(f'GRANT ALL ON {table} TO PUBLIC, "{runtime}"'))
            await connection.execute(text(f'GRANT UPDATE (id) ON {table} TO "{runtime}"'))
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "398d4e5f6071"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert migration.returncode == 0, "Synthetic audit removal migration failed"
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
                    "AND c.relname=ANY(:tables) AND NOT t.tgisinternal"
                ),
                {"tables": list(tables)},
            )
            == 0
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname IN ('prevent_rag_runtime_transition_mutation', "
                    "'prevent_checkin_audit_mutation','prevent_rag_evidence_citation_mutation')"
                )
            )
            == 0
        )
    # No application privileges survive the interval before provisioning.
    with pytest.raises(DBAPIError) as error:
        async with reader.begin() as connection:
            await connection.execute(text("UPDATE rag_runtime_environment_transition SET environment_revision=9"))
    assert error.value.orig.sqlstate == "42501"
    for _ in range(2):
        await _grant_historical_test_permissions(admin, environment)
        for table in tables:
            for sql in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}", f"TRUNCATE {table}"):
                with pytest.raises(DBAPIError) as error:
                    async with reader.begin() as connection:
                        await connection.execute(text(sql))
                assert error.value.orig.sqlstate == "42501"
    runtime_sessions = async_sessionmaker(reader, expire_on_commit=False)
    async with runtime_sessions.begin() as session:
        transition = await RagRuntimeEnvironmentTransitionService(RagRuntimeRepository(session)).transition(
            RagRuntimeEnvironmentTransitionCreate(
                environment_id=UUID(env_id),
                transition_kind=RagRuntimeEnvironmentTransitionKind.SUSPEND,
                expected_environment_revision=1,
                expected_safety_epoch=1,
                expected_active_bundle_id=None,
                expected_active_bundle_manifest_hash=None,
                expected_governance_revision_ref=None,
                guard_decision_ref="synthetic-guard-next",
                transition_reason_code="SYNTHETIC_SAFETY_HOLD",
                created_by="synthetic-runtime",
            )
        )
        assert transition.environment_revision == 2
    async with reader.connect() as connection:
        environment_row = (
            await connection.execute(
                text("SELECT environment_status,environment_revision FROM rag_runtime_environment WHERE id=:id"),
                {"id": env_id},
            )
        ).one()
        assert environment_row == ("SUSPENDED", 2)
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM rag_runtime_environment_transition WHERE environment_id=:id"),
                {"id": env_id},
            )
            == 2
        )

    # Both writers pass the same optimistic preconditions. The environment row
    # lock serializes them, so the loser observes revision 2 and fails closed.
    # Canonical Runtime Environment는 DB당 최대 4개이므로 shared disposable DB fixture는 canonical code를 명시적으로 분리한다.
    concurrent_env_id = uuid4()
    async with admin.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_runtime_environment (id,environment_code,environment_status) "
                "VALUES (:id,:code,'ACTIVE')"
            ),
            {"id": str(concurrent_env_id), "code": "CLOSED_DEMO"},
        )

    async def suspend_concurrently(actor: str):
        async with runtime_sessions.begin() as session:
            return await RagRuntimeEnvironmentTransitionService(RagRuntimeRepository(session)).transition(
                RagRuntimeEnvironmentTransitionCreate(
                    environment_id=concurrent_env_id,
                    transition_kind=RagRuntimeEnvironmentTransitionKind.SUSPEND,
                    expected_environment_revision=1,
                    expected_safety_epoch=1,
                    expected_active_bundle_id=None,
                    expected_active_bundle_manifest_hash=None,
                    expected_governance_revision_ref=None,
                    guard_decision_ref=f"synthetic-guard:{actor}",
                    transition_reason_code="SYNTHETIC_CONCURRENT_HOLD",
                    created_by=actor,
                )
            )

    outcomes = await asyncio.gather(
        suspend_concurrently("synthetic-runtime-a"),
        suspend_concurrently("synthetic-runtime-b"),
        return_exceptions=True,
    )
    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, RuntimeEnvironmentTransitionConflictError) for outcome in outcomes) == 1
    async with reader.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT environment_revision FROM rag_runtime_environment WHERE id=:id"),
                {"id": str(concurrent_env_id)},
            )
            == 2
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM rag_runtime_environment_transition WHERE environment_id=:id"),
                {"id": str(concurrent_env_id)},
            )
            == 1
        )
    with pytest.raises(DBAPIError) as error:
        async with producer.begin() as connection:
            await connection.execute(text("DELETE FROM rag_runtime_environment_transition"))
    assert error.value.orig.sqlstate == "42501"
    rollback = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "downgrade", "398c3d4e5f60"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rollback.returncode != 0
    assert "Audit trigger removal cannot be downgraded" in rollback.stderr
    return env_id


_NORMALIZED_IMAGE_FIXTURE_COLUMNS = {
    "normalized_object_key": "varchar(500)",
    "normalized_width": "integer",
    "normalized_height": "integer",
}


async def _add_normalized_image_fixture_columns(connection):
    """#809 컬럼을 고정 revision 스키마에 잠시 보충합니다.

    이 테스트는 `398b2c3d4e5f`에 고정해 그 시점의 권한 전이를 검증하는데, 공용 ORM 헬퍼에는
    이후 추가된 medical_document 컬럼이 들어 있습니다. 검증 대상 revision을 올리지 않으려고
    부족한 컬럼만 채우고, 뒤따르는 head 업그레이드가 같은 컬럼을 정상적으로 추가할 수 있도록
    사용이 끝나면 즉시 되돌립니다.
    """
    for name, column_type in _NORMALIZED_IMAGE_FIXTURE_COLUMNS.items():
        await connection.execute(text(f"ALTER TABLE medical_document ADD COLUMN IF NOT EXISTS {name} {column_type}"))


async def _drop_normalized_image_fixture_columns(connection):
    for name in _NORMALIZED_IMAGE_FIXTURE_COLUMNS:
        await connection.execute(text(f"ALTER TABLE medical_document DROP COLUMN IF EXISTS {name}"))


async def _exercise_prescription_candidate_cutover(admin, reader, environment):
    from app.models.rag_candidate import MedicationCandidateSearchStatus
    from app.repositories.medication_candidate_repository import MedicationCandidateRepository
    from app.repositories.prescription_repository import PrescriptionRepository
    from app.tests.rag.test_medication_candidate_repository import _ready_result
    from app.tests.repositories.test_medication_schedule_repository_integration import (
        _create_active_version_medication,
        _create_user_with_self_profile,
    )

    async with admin.begin() as connection:
        await _add_normalized_image_fixture_columns(connection)

    sessions = async_sessionmaker(admin, expire_on_commit=False)
    async with sessions.begin() as session:
        owner, profile = await _create_user_with_self_profile(session, label="cutover-prescription")
        prescription, medication = await _create_active_version_medication(session, owner=owner, profile=profile)
        repository = MedicationCandidateRepository(session)
        search = await repository.create_search(
            prescription_version_medication_id=medication.id,
            medication_name_snapshot=medication.medication_name,
            strength_text_snapshot=None,
            query_digest="a" * 64,
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )
        _, results = await repository.assemble_and_finalize_search(
            search=search,
            results=[_ready_result()],
            status=MedicationCandidateSearchStatus.READY,
            finalized_at=datetime.now(UTC),
        )
        result_id = results[0].id
        owner_id = owner.id
        prescription_id = prescription.id
        active_version_id = prescription.active_version_id
        medication_id = medication.id
        search_id = search.id
        original_hash = await session.scalar(
            text("SELECT content_hash FROM prescription_version WHERE id=:id"),
            {"id": str(active_version_id)},
        )

    # 고정 revision 스키마로 되돌립니다. 이후 head 업그레이드가 이 컬럼들을 정식으로 추가합니다.
    async with admin.begin() as connection:
        await _drop_normalized_image_fixture_columns(connection)

    # Stored graph validation must fail before removing any trigger or column.
    async with admin.begin() as connection:
        await connection.execute(text("ALTER TABLE prescription_version DISABLE TRIGGER ALL"))
        await connection.execute(
            text("UPDATE prescription_version SET content_hash=repeat('f',64) WHERE id=:id"),
            {"id": str(active_version_id)},
        )
        await connection.execute(text("ALTER TABLE prescription_version ENABLE TRIGGER ALL"))
    invalid_prescription = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "3980718293a4"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert invalid_prescription.returncode != 0
    async with admin.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "398d4e5f6071"
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='prescription_version'::regclass "
                    "AND NOT tgisinternal"
                )
            )
            == 4
        )
        assert await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='prescription_version' AND column_name='assembly_xid')"
            )
        )
        await connection.execute(text("ALTER TABLE prescription_version DISABLE TRIGGER ALL"))
        await connection.execute(
            text("UPDATE prescription_version SET content_hash=:hash WHERE id=:id"),
            {"hash": original_hash, "id": str(active_version_id)},
        )
        await connection.execute(text("ALTER TABLE prescription_version ENABLE TRIGGER ALL"))

        # Reproduce an invalid committed Candidate graph that an old privileged tool
        # could have left behind. The forward migration must detect and refuse it.
        await connection.execute(text("ALTER TABLE medication_candidate_search DISABLE TRIGGER ALL"))
        await connection.execute(
            text("UPDATE medication_candidate_search SET candidate_count=candidate_count+1 WHERE id=:id"),
            {"id": str(search_id)},
        )
        await connection.execute(text("ALTER TABLE medication_candidate_search ENABLE TRIGGER ALL"))
    invalid_candidate = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "3980718293a4"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert invalid_candidate.returncode != 0
    async with admin.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "398d4e5f6071"
        await connection.execute(text("ALTER TABLE medication_candidate_search DISABLE TRIGGER ALL"))
        await connection.execute(
            text("UPDATE medication_candidate_search SET candidate_count=candidate_count-1 WHERE id=:id"),
            {"id": str(search_id)},
        )
        await connection.execute(text("ALTER TABLE medication_candidate_search ENABLE TRIGGER ALL"))
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "3980718293a4"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert migration.returncode == 0, "Synthetic Prescription/Candidate removal failed"
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal"
                )
            )
            == 0
        )
        assert not await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='prescription_version' AND column_name='assembly_xid')"
            )
        )
    await _grant_historical_test_permissions(admin, environment)
    runtime_sessions = async_sessionmaker(reader, expire_on_commit=False)
    async with runtime_sessions.begin() as session:
        candidates = MedicationCandidateRepository(session)
        assert (
            await candidates.get_medication_for_candidate_search_owned(
                prescription_version_medication_id=medication_id,
                user_id=owner_id,
            )
            is not None
        )
        assert (
            await candidates.get_result_selection_for_update_owned(
                candidate_search_result_id=result_id,
                user_id=owner_id,
            )
            is not None
        )
        prescriptions = PrescriptionRepository(session)
        current = await prescriptions.get_owned_for_version_update(prescription_id=prescription_id, user_id=owner_id)
        assert current is not None
        version = await prescriptions.create_version(
            prescription=current,
            prescribed_date=current.prescribed_date,
            confirmed_at=datetime.now(UTC),
            medications=[{"medication_name": "합성정정약", "frequency_per_day": 1, "display_order": 1}],
        )
        loaded = await prescriptions.get_version_medications(prescription_version_id=version.id)
        assert len(loaded) == version.medication_count == 1
        assert loaded[0].medication_name == "합성정정약"
    for table in ("prescription_version", "prescription_version_medication", "medication_candidate_search_result"):
        for sql in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}", f"TRUNCATE {table}"):
            with pytest.raises(DBAPIError) as error:
                async with reader.begin() as connection:
                    await connection.execute(text(sql))
            assert error.value.orig.sqlstate == "42501"
    rollback = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "downgrade", "398d4e5f6071"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rollback.returncode != 0
    assert "Prescription/Candidate trigger removal cannot be downgraded" in rollback.stderr


async def _assert_runtime_transition_revisions_are_sealed(admin, environment_id: str, environment) -> None:
    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "downgrade", "398e5f607182"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert downgrade.returncode == 0, "Synthetic Runtime revision constraint downgrade failed"
    duplicate_id = str(uuid4())
    async with admin.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_runtime_environment_transition "
                "(id,environment_id,transition_kind,environment_revision,safety_epoch,guard_decision_ref) "
                "VALUES (:id,:environment,'SUSPEND',2,1,'synthetic-preexisting-duplicate')"
            ),
            {"id": duplicate_id, "environment": environment_id},
        )
    rejected = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "3980718293a4"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rejected.returncode != 0
    assert "duplicate revisions exist" in rejected.stderr
    async with admin.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "398e5f607182"
        await connection.execute(
            text("DELETE FROM rag_runtime_environment_transition WHERE id=:id"),
            {"id": duplicate_id},
        )
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"), "upgrade", "3980718293a4"],
        cwd=ROOT,
        env={**os.environ, "DB_NAME": environment["DB_NAME"]},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert upgrade.returncode == 0, "Synthetic Runtime revision sealing migration failed"
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema='public' AND table_name='rag_runtime_environment_transition' "
                    "AND constraint_name='uq_rag_runtime_transition_environment_revision' "
                    "AND constraint_type='UNIQUE'"
                )
            )
            == 1
        )
    with pytest.raises(DBAPIError) as error:
        async with admin.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO rag_runtime_environment_transition "
                    "(id,environment_id,transition_kind,environment_revision,safety_epoch,guard_decision_ref) "
                    "VALUES (:id,:environment,'SUSPEND',2,1,'synthetic-duplicate-revision')"
                ),
                {"id": str(uuid4()), "environment": environment_id},
            )
    assert error.value.orig.sqlstate == "23505"


async def _grant_historical_test_permissions(admin, environment):
    """Explicit fixture grants for pre-#429 schema stages, never deployment provisioning.

    Today's role policy requires the current schema. Historical migration tests use
    just the old DML boundary to exercise cutover checks; real provisioning is tested
    separately after upgrading to head, with actual Runtime/Writer credentials.
    """
    runtime, writer = environment["DB_APP_USER"], environment["SOURCE_WRITER_USER"]
    async with admin.begin() as connection:
        present = set(await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
        for tables, privileges in (
            (RUNTIME_MUTABLE_TABLES, "SELECT, INSERT, UPDATE, DELETE"),
            (RUNTIME_APPEND_ONLY_TABLES | CATALOG_TABLES, "SELECT, INSERT"),
        ):
            for table in tables - {
                "ai_job_intake_context",
                "ai_job_execution_context",
                "ai_job_execution_identification",
                "account_deletion_request",  # Added after the historical Source cutover.
                "medication_schedule_audit",  # Added after the historical Source cutover.
                "push_subscription",  # #469 does not exist at the historical revision.
                "push_delivery",
                "guide_feedback",  # #633 follows the historical Source cutover.
                "chat_message_feedback",
                "retrieval_signal",  # #178/#689 added after historical cutover.
                "retrieval_hit",
                "rag_request_guard_authority",  # #713 follows the historical Source cutover.
                "rag_request_source_decision",
                "rag_request_member_decision",
                "rag_request_guard_runtime_binding",  # #806 follows the historical Source cutover.
                "rag_evidence_authority",  # #712 follows the historical Source cutover.
            }:
                await connection.execute(text(f'GRANT {privileges} ON "{table}" TO "{runtime}"'))
        for table in set(SOURCE_TABLES) & present:
            await connection.execute(text(f'GRANT SELECT ON "{table}" TO "{runtime}"'))
            await connection.execute(text(f'GRANT SELECT, INSERT ON "{table}" TO "{writer}"'))
        for table in ("rag_source_operation", "rag_source_ingestion_run"):
            await connection.execute(text(f'GRANT UPDATE ON "{table}" TO "{writer}"'))
        await connection.execute(
            text(f'GRANT UPDATE (verification_status,verified_at,effective_at) ON rag_source_snapshot TO "{writer}"')
        )


async def _add_auth_fixture_columns(connection):
    for table, columns in RUNTIME_AUTH_UPDATE_COLUMNS.items():
        for name in columns:
            await connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" text'))


async def _add_account_deletion_request_fixture_columns(connection):
    columns = {
        "user_id": "uuid",
        "status": ("varchar(32) CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED'))"),
        "requested_at": "timestamptz",
        "started_at": "timestamptz",
        "completed_at": "timestamptz",
        "failed_at": "timestamptz",
        "retry_count": "integer CHECK (retry_count >= 0)",
        "last_error_code": "varchar(128)",
        "created_at": "timestamptz",
        "updated_at": "timestamptz",
    }
    for name, column_type in columns.items():
        await connection.execute(text(f"ALTER TABLE account_deletion_request ADD COLUMN {name} {column_type}"))


async def _add_account_withdrawal_cleanup_fixture_columns(connection):
    for table, columns in ACCOUNT_WITHDRAWAL_CLEANUP_UPDATE_COLUMNS.items():
        existing = set(
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:table"
                ),
                {"table": table},
            )
        )
        for column in set(columns) - existing:
            await connection.execute(
                text(f"ALTER TABLE {quoted_identifier(table)} ADD COLUMN {quoted_identifier(column)} text")
            )


async def _exercise_preflight_context_runtime_permissions(reader, producer):
    from app.tests.rag.test_ai_job_preflight_context_repository import persist_and_verify_chat_context

    async with async_sessionmaker(reader, expire_on_commit=False).begin() as session:
        await persist_and_verify_chat_context(session)
    for table in ("ai_job_intake_context", "ai_job_execution_context", "ai_job_execution_identification"):
        for engine, statements in (
            (reader, (f"UPDATE {table} SET id=id", f"DELETE FROM {table}", f"TRUNCATE {table}")),
            (producer, (f"SELECT * FROM {table}", f"INSERT INTO {table} DEFAULT VALUES")),
        ):
            for statement in statements:
                with pytest.raises(DBAPIError) as error:
                    async with engine.begin() as connection:
                        await connection.execute(text(statement))
                assert error.value.orig.sqlstate == "42501"


async def _exercise_feedback_runtime_permissions(reader, producer):
    for table in ("guide_feedback", "chat_message_feedback"):
        async with reader.begin() as connection:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert await connection.scalar(
                    text("SELECT has_table_privilege(current_user, :table, :privilege)"),
                    {"table": table, "privilege": privilege},
                )
            await connection.execute(text(f"SELECT * FROM {table}"))
            await connection.execute(text(f"UPDATE {table} SET rating=rating WHERE false"))
            await connection.execute(text(f"DELETE FROM {table} WHERE false"))
        for engine, statements in (
            (reader, (f"TRUNCATE {table}",)),
            (producer, (f"SELECT * FROM {table}", f"INSERT INTO {table} DEFAULT VALUES")),
        ):
            for statement in statements:
                with pytest.raises(DBAPIError) as error:
                    async with engine.begin() as connection:
                        await connection.execute(text(statement))
                assert error.value.orig.sqlstate == "42501"


async def _exercise_notification_runtime_permissions(reader, producer):
    from datetime import timedelta

    from app.commands.process_notifications import process_notifications_once
    from app.models.user_consents import ConsentPurpose, ConsentStatus
    from app.repositories.user_consent_repository import UserConsentRepository
    from app.services.user_consent_policy import current_consent_policy_version
    from app.tests.notifications.test_notifications import NOW
    from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
    from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

    factory = async_sessionmaker(reader, expire_on_commit=False)
    async with factory.begin() as session:
        owner, profile = await _create_user_with_self_profile(session, label="notification-runtime-synthetic")
        await _create_occurrence(session, owner=owner, profile=profile, deadline_at=NOW + timedelta(hours=4))
        # #621: publish_once()의 ConsentGateService가 NOTIFICATION 미동의 사용자를 걸러내므로,
        # 이 fixture도 실제 Runtime 권한 경계(reader)를 통해 동의를 저장해야 배포 후 실제
        # 허용 경로(1/1)를 검증한다.
        await UserConsentRepository(session).set_status(
            user_id=owner.id,
            purpose=ConsentPurpose.NOTIFICATION,
            status=ConsentStatus.GRANTED,
            policy_version=current_consent_policy_version(ConsentPurpose.NOTIFICATION),
            changed_at=NOW,
        )
    result = await process_notifications_once(now=NOW, session_factory=factory)
    assert result.created_count == result.delivered_count == 1
    assert (await process_notifications_once(now=NOW, session_factory=factory)).delivered_count == 0
    await _assert_runtime_delete_policy(reader, "notification_record")
    for engine, statements in (
        (reader, ("TRUNCATE notification_record",)),
        (producer, ("SELECT * FROM notification_record", "INSERT INTO notification_record DEFAULT VALUES")),
    ):
        for statement in statements:
            await _assert_permission_denied(engine, statement)


async def _exercise_checkin_correction_runtime_permissions(admin, reader, producer, *, with_history):
    """Both backlog correction and subsequent schedule correction use Runtime credentials."""
    from app.models.medication_schedules import MedicationCheckinStatus
    from app.models.track_c import BarrierResponse, SafetyAssessment, SupportActionPlan
    from app.repositories.medication_checkin_repository import MedicationCheckinRepository
    from app.repositories.track_c_storage_repository import TrackCStorageRepository
    from app.services.medication_checkins import MedicationCheckinService
    from app.services.track_c_revision_invalidation import TrackCCheckinRevisionInvalidation
    from app.tests.repositories.test_medication_checkin_repository_integration import _create_occurrence
    from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

    async with async_sessionmaker(admin, expire_on_commit=False)() as session:
        owner, profile = await _create_user_with_self_profile(session, label="issue668-runtime")
        occurrence = await _create_occurrence(
            session, owner=owner, profile=profile, deadline_at=datetime(2026, 9, 16, 4, tzinfo=UTC)
        )
        repository = MedicationCheckinRepository(session)
        await repository.create_if_absent(
            occurrence_id=occurrence.id, status=MedicationCheckinStatus.UNCONFIRMED, taken_at=None
        )
        await repository.close_occurrence(occurrence=occurrence)
        await session.commit()
        owner_id, occurrence_id = owner.id, occurrence.id

    for revision, status in ((1, MedicationCheckinStatus.NOT_TAKEN), (2, MedicationCheckinStatus.TAKEN)):
        async with async_sessionmaker(reader, expire_on_commit=False)() as session:
            service = MedicationCheckinService(
                MedicationCheckinRepository(session),
                revision_invalidation=TrackCCheckinRevisionInvalidation(TrackCStorageRepository(session)),
            )
            result = await service.put_owned(
                occurrence_id=occurrence_id,
                user_id=owner_id,
                status=status,
                taken_at=None,
                expected_revision=revision,
            )
            await session.commit()
            assert result.status == status
            assert result.revision == revision + 1

        if revision == 1 and with_history:
            async with async_sessionmaker(admin, expire_on_commit=False)() as session:
                safety = SafetyAssessment(
                    medication_checkin_id=result.checkin_id,
                    checkin_revision=2,
                    revision=1,
                    symptom_codes=[],
                    response_level="ROUTINE",
                    safety_disposition="NORMAL",
                    message_code="SYNTHETIC",
                    copy_version="synthetic-v1",
                    source_version="synthetic-v1",
                )
                session.add(safety)
                await session.flush()
                barrier = BarrierResponse(
                    medication_checkin_id=result.checkin_id,
                    checkin_revision=2,
                    safety_assessment_id=safety.id,
                    revision=1,
                    response_status="ANSWERED",
                    barrier_code="FORGOT",
                )
                session.add(barrier)
                await session.flush()
                active = SupportActionPlan(
                    barrier_response_id=barrier.id,
                    support_code="REMINDER_SETUP",
                    rule_version="synthetic-v1",
                    copy_version="synthetic-v1",
                    action_config_snapshot={},
                    status="ACTIVE",
                )
                completed = SupportActionPlan(
                    barrier_response_id=barrier.id,
                    support_code="REMINDER_SETUP",
                    rule_version="synthetic-v1",
                    copy_version="synthetic-v1",
                    action_config_snapshot={},
                    status="COMPLETED",
                    completed_at=datetime(2026, 9, 16, 5, tzinfo=UTC),
                )
                session.add_all([active, completed])
                await session.commit()
                active_id, completed_id = str(active.id), str(completed.id)

    async with reader.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM checkin_audit WHERE checkin_id=:id"), {"id": str(result.checkin_id)}
            )
            == 2
        )

    if with_history:
        async with reader.connect() as connection:
            assert (
                await connection.scalar(text("SELECT status FROM support_action_plan WHERE id=:id"), {"id": active_id})
                == "CANCELLED"
            )
            assert await connection.scalar(
                text("SELECT cancelled_at IS NOT NULL FROM support_action_plan WHERE id=:id"), {"id": active_id}
            )
            assert (
                await connection.scalar(
                    text("SELECT status FROM support_action_plan WHERE id=:id"), {"id": completed_id}
                )
                == "COMPLETED"
            )
            assert (
                await connection.scalar(
                    text("SELECT symptom_codes FROM safety_assessment WHERE id=:id"), {"id": str(safety.id)}
                )
                == []
            )
            assert (
                await connection.scalar(
                    text("SELECT barrier_code FROM barrier_response WHERE id=:id"), {"id": str(barrier.id)}
                )
                == "FORGOT"
            )
        for table in RUNTIME_CHECKIN_LOCK_TABLES:
            async with reader.begin() as connection:
                await connection.execute(text(f"SELECT id FROM {table} FOR UPDATE"))
                await connection.execute(text(f"UPDATE {table} SET checkin_lock_marker=0"))
            with pytest.raises(DBAPIError) as error:
                async with reader.begin() as connection:
                    await connection.execute(text(f"UPDATE {table} SET checkin_lock_marker=1"))
            assert error.value.orig.sqlstate == "23514"
        await _exercise_track_c_followup_correction(reader, plan_id=UUID(completed_id), user_id=owner_id)

    for table in sorted(RUNTIME_CHECKIN_LOCK_TABLES | {"support_action_plan"}):
        await _assert_runtime_delete_policy(reader, table)
        # #820: 새 revision INSERT는 허용된다. 이력 변경·삭제만 막혀 있어야 한다.
        for engine, sql in (
            (reader, f"TRUNCATE {table}"),
            (reader, f"UPDATE {table} SET id=id"),
            (producer, f"SELECT * FROM {table}"),
        ):
            await _assert_permission_denied(engine, sql)
    for sql in (
        "UPDATE safety_assessment SET symptom_codes='[]'",
        "UPDATE barrier_response SET revision=revision+1",
        "UPDATE support_action_plan SET action_config_snapshot='{}'",
        "UPDATE support_action_plan SET completed_at=now()",
    ):
        with pytest.raises(DBAPIError) as error:
            async with reader.begin() as connection:
                await connection.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"


async def _exercise_track_c_followup_correction(reader, *, plan_id, user_id) -> None:
    """#820: Follow-up 최초 응답과 정정이 Runtime 권한만으로 끝까지 돌아야 한다.

    정정은 audit INSERT와 response/revision/updated_at UPDATE를 함께 낸다.
    audit에 SELECT를 주지 않아도 append가 되는지 여기서 확인한다.
    """
    from app.models.track_c import ActionPlanFollowupResponse
    from app.repositories.track_c_storage_repository import TrackCStorageRepository

    for revision, response in ((1, ActionPlanFollowupResponse.HELPED), (2, ActionPlanFollowupResponse.NOT_SURE)):
        async with async_sessionmaker(reader, expire_on_commit=False)() as session:
            repository = TrackCStorageRepository(session)
            saved = await repository.save_plan_followup(
                plan_id=plan_id,
                current=await repository.get_plan_followup_for_update(plan_id=plan_id),
                response=response,
                user_id=user_id,
                changed_at=datetime(2026, 9, 16, 6, tzinfo=UTC),
            )
            await session.commit()
            assert saved.revision == revision
            assert saved.response == response


async def _add_checkin_lock_fixture_columns(connection):
    for table in RUNTIME_CHECKIN_LOCK_TABLES:
        await connection.execute(
            text(
                f'ALTER TABLE "{table}" ADD COLUMN checkin_lock_marker integer NOT NULL DEFAULT 0 CHECK (checkin_lock_marker=0)'
            )
        )
    await connection.execute(
        text("ALTER TABLE support_action_plan ADD COLUMN status text, ADD COLUMN cancelled_at timestamptz")
    )


# #820: follow-up 컬럼 단위 GRANT가 참조하는 컬럼. 운영 스키마와 같은 이름으로 합성 fixture에도 만든다.
_TRACK_C_FOLLOWUP_FIXTURE_COLUMN_TYPES = {
    "response": "text",
    "revision": "integer",
    "updated_at": "timestamptz",
}


async def _add_track_c_followup_fixture_columns(connection):
    """#820: 컬럼 단위 GRANT 대상 컬럼이 합성 fixture에 없으면 provisioning이 깨진다."""
    assert set(RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS) == set(_TRACK_C_FOLLOWUP_FIXTURE_COLUMN_TYPES), (
        "GRANT 대상 컬럼이 바뀌면 fixture 컬럼도 함께 갱신해야 한다"
    )
    columns = ", ".join(
        f"ADD COLUMN {quoted_identifier(column)} {_TRACK_C_FOLLOWUP_FIXTURE_COLUMN_TYPES[column]}"
        for column in RUNTIME_TRACK_C_FOLLOWUP_UPDATE_COLUMNS
    )
    await connection.execute(text(f"ALTER TABLE action_plan_followup {columns}"))


async def _exercise_retrieval_run_runtime_permissions(reader, producer, admin) -> None:
    from decimal import Decimal

    from ai_worker.adapters.sqlalchemy_retrieval_run import SqlAlchemyRetrievalRunStore
    from ai_worker.tasks.rag.retrieval_run import (
        BeginRetrievalRunRequest,
        BeginRetrievalRunSuccess,
        FinalizeRetrievalRunRequest,
        FinalizeRetrievalRunSuccess,
        PersistedHitInput,
        PersistedSignalInput,
    )

    user_id = uuid4()
    job_id = uuid4()
    ctx_id = uuid4()
    index_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()

    # Prerequisites: seed knowledge index, document, and chunk using admin (owner)
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO rag_knowledge_index (id, index_code, index_version, corpus_manifest_hash, "
                "embedding_manifest_hash, index_configuration_hash, embedding_model_ref, "
                "embedding_model_version, embedding_dimension, distance_metric, member_count) "
                "VALUES (:id, 'TEST_IDX', '1.0', :h, :h, :h, 'text-embedding-3-large', '1.0', 1536, 'COSINE', 1)"
            ),
            {"id": str(index_id), "h": "a" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_document (id, title, source_url, document_version, document_status, "
                "record_contract_version, publisher) "
                "VALUES (:id, 'Test Doc', :url, '1.0', 'ACTIVE', 'LEGACY_V1', 'Publisher')"
            ),
            {"id": str(doc_id), "url": f"https://example.invalid/{uuid4()}"},
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                "content_hash, normalization_version) "
                "VALUES (:id, :doc_id, 0, '테스트 청크 내용', :h, 'v1')"
            ),
            {"id": str(chunk_id), "doc_id": str(doc_id), "h": "b" * 64},
        )

    # Runtime user and ai_job created by reader (DB_APP_USER has SELECT, INSERT, UPDATE, DELETE on user, ai_job)
    async with reader.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                "VALUES (:id, :email, 'synthetic-hash', '합성사용자', true, false)"
            ),
            {"id": str(user_id), "email": f"test-{uuid4().hex[:8]}@example.invalid"},
        )
        await conn.execute(
            text(
                "INSERT INTO ai_job (id, user_id, job_type, status, max_attempts, attempt_count) "
                "VALUES (:id, :uid, 'OCR', 'PENDING', 3, 0)"
            ),
            {"id": str(job_id), "uid": str(user_id)},
        )

    # 1 & 2: SqlAlchemyRetrievalRunStore.begin_run() succeeds with DB_APP_USER session
    store = SqlAlchemyRetrievalRunStore(async_sessionmaker(reader, expire_on_commit=False, autoflush=False))
    begin_req = BeginRetrievalRunRequest(
        job_id=job_id,
        node_id="hybrid_retrieve",
        execution_context_id=ctx_id,
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="3" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="4" * 64,
        runtime_guard_decision_ref="synthetic-guard-ref",
        knowledge_index_id=index_id,
        variant="RET-H",
        query_digest_algorithm="sha256",
        query_digest_key_version="v1",
        query_digest="1" * 64,
        filter_snapshot={"code": "ASPIRIN"},
        filter_snapshot_hash="5" * 64,
        source_manifest_hash="6" * 64,
        retrieval_configuration_hash="2" * 64,
        lexical_limit=20,
        dense_limit=20,
        hybrid_limit=30,
        final_k=5,
        query_embedding_sha256="7" * 64,
    )
    outcome = await store.begin_run(begin_req)
    assert isinstance(outcome, BeginRetrievalRunSuccess)
    assert outcome.is_resumed is False
    run_id = outcome.run_id

    # 3: finalize_run() succeeds with DB_APP_USER session
    sig = PersistedSignalInput(
        knowledge_chunk_id=chunk_id,
        method="EXACT",
        raw_rank=1,
        raw_score=Decimal("1.0"),
        score_projection_version="observed-stage-score-decimal@1",
    )
    hit = PersistedHitInput(
        knowledge_chunk_id=chunk_id,
        rrf_rank=1,
        rrf_score=Decimal("0.016393442622950820"),
        rrf_score_numerator="1",
        rrf_score_denominator="61",
        final_rank=1,
        selected=True,
        lexical_rank=1,
        dense_rank=1,
    )
    fin_req = FinalizeRetrievalRunRequest(
        run_id=run_id,
        status="COMPLETED",
        search_receipt_hash="8" * 64,
        signals=(sig,),
        hits=(hit,),
    )
    fin_outcome = await store.finalize_run(fin_req)
    assert isinstance(fin_outcome, FinalizeRetrievalRunSuccess)
    assert fin_outcome.receipt.receipt_hash is not None

    # 4: Runtime session can SELECT run, signal, and hit
    async with reader.begin() as conn:
        run_row = (
            await conn.execute(
                text("SELECT status, receipt_hash FROM retrieval_run WHERE id = :id"),
                {"id": str(run_id)},
            )
        ).first()
        assert run_row is not None
        assert run_row.status == "COMPLETED"
        assert run_row.receipt_hash == fin_outcome.receipt.receipt_hash

        sig_count = await conn.scalar(
            text("SELECT count(*) FROM retrieval_signal WHERE retrieval_run_id = :id"),
            {"id": str(run_id)},
        )
        assert sig_count == 1

        hit_count = await conn.scalar(
            text("SELECT count(*) FROM retrieval_hit WHERE retrieval_run_id = :id"),
            {"id": str(run_id)},
        )
        assert hit_count == 1

    # 5: retrieval_signal UPDATE rejected
    with pytest.raises(DBAPIError) as error:
        async with reader.begin() as conn:
            await conn.execute(
                text("UPDATE retrieval_signal SET raw_rank = 2 WHERE retrieval_run_id = :id"),
                {"id": str(run_id)},
            )
    assert error.value.orig.sqlstate == "42501"

    # 6: retrieval_hit UPDATE rejected
    with pytest.raises(DBAPIError) as error:
        async with reader.begin() as conn:
            await conn.execute(
                text("UPDATE retrieval_hit SET final_rank = 2 WHERE retrieval_run_id = :id"),
                {"id": str(run_id)},
            )
    assert error.value.orig.sqlstate == "42501"

    # 7: withdrawal cleanup can delete retrieval_run directly; child evidence remains protected.
    for table in ("retrieval_run", "retrieval_signal", "retrieval_hit"):
        await _assert_runtime_delete_policy(reader, table)

    # 8: TRUNCATE rejected on all 3 tables
    for table in ("retrieval_run", "retrieval_signal", "retrieval_hit"):
        with pytest.raises(DBAPIError) as error:
            async with reader.begin() as conn:
                await conn.execute(text(f"TRUNCATE {table}"))
        assert error.value.orig.sqlstate == "42501"

    # 9: DB_APP_USER Source write rejection maintained
    for stmt in (
        "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status) VALUES ('00000000-0000-0000-0000-000000000001', 'SRC', 'SRC', 'ACTIVE')",
        "UPDATE rag_source SET display_name = 'changed'",
        "DELETE FROM rag_source",
    ):
        with pytest.raises(DBAPIError) as error:
            async with reader.begin() as conn:
                await conn.execute(text(stmt))
        assert error.value.orig.sqlstate == "42501"

    # 10: DB_APP_USER Knowledge Index write rejection maintained
    for stmt in (
        "INSERT INTO rag_knowledge_index (id, index_code) VALUES ('00000000-0000-0000-0000-000000000002', 'IDX')",
        "INSERT INTO knowledge_document (id, title) VALUES ('00000000-0000-0000-0000-000000000003', 'Doc')",
        "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text) VALUES ('00000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000003', 0, 'text')",
        "DELETE FROM rag_knowledge_index",
    ):
        with pytest.raises(DBAPIError) as error:
            async with reader.begin() as conn:
                await conn.execute(text(stmt))
        assert error.value.orig.sqlstate == "42501"

    # Producer (writer) also has no access to retrieval tables
    for table in ("retrieval_run", "retrieval_signal", "retrieval_hit"):
        for stmt in (f"SELECT * FROM {table}", f"INSERT INTO {table} DEFAULT VALUES"):
            with pytest.raises(DBAPIError) as error:
                async with producer.begin() as conn:
                    await conn.execute(text(stmt))
            assert error.value.orig.sqlstate == "42501"

    # 11: ai_job parent delete -> Retrieval child cascade cleanup confirmed
    async with reader.begin() as conn:
        await conn.execute(text("DELETE FROM ai_job WHERE id = :id"), {"id": str(job_id)})

    async with reader.begin() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM retrieval_run WHERE id = :id"), {"id": str(run_id)}) == 0
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM retrieval_signal WHERE retrieval_run_id = :id"),
                {"id": str(run_id)},
            )
            == 0
        )
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM retrieval_hit WHERE retrieval_run_id = :id"),
                {"id": str(run_id)},
            )
            == 0
        )

    # Cleanup user with reader and seeded knowledge with admin
    async with reader.begin() as conn:
        await conn.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": str(user_id)})

    async with admin.begin() as conn:
        await conn.execute(text("DELETE FROM knowledge_chunk WHERE id = :id"), {"id": str(chunk_id)})
        await conn.execute(text("DELETE FROM knowledge_document WHERE id = :id"), {"id": str(doc_id)})
        await conn.execute(text("DELETE FROM rag_knowledge_index WHERE id = :id"), {"id": str(index_id)})


@pytest_asyncio.fixture
async def database(monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.engine import make_url

    def _alembic_config() -> Config:
        alembic_config = Config()
        alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
        return alembic_config

    name = "retrieval_acl_" + uuid4().hex
    original = config.database_url
    cluster = create_async_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = create_async_engine(make_url(original).set(database=name), hide_parameters=True)
    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setattr(config, "DB_NAME", name)
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        yield engine
    finally:
        await engine.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await cluster.dispose()


async def test_retrieval_run_provisioned_runtime_role_lifecycle(database) -> None:
    from infra.python.provision_database_roles import provision_roles

    admin = database
    suffix = uuid4().hex[:12]
    runtime, writer = (f"retacl_{part}_{suffix}" for part in ("runtime", "writer"))
    password = f"synthetic-{suffix}-only"
    reader = create_async_engine(admin.url.set(username=runtime, password=password))
    producer = create_async_engine(admin.url.set(username=writer, password=password))
    try:
        async with admin.begin() as connection:
            for role in (runtime, writer):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            await provision_roles(
                connection,
                owner=config.DB_USER,
                runtime=runtime,
                writer=writer,
            )
        await _exercise_retrieval_run_runtime_permissions(reader, producer, admin)
    finally:
        await reader.dispose()
        await producer.dispose()
        async with admin.begin() as connection:
            for role in (runtime, writer):
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


async def test_runtime_catalog_and_knowledge_read_acl_parity_without_optional_roles(database) -> None:
    """#779: Verify Runtime Catalog & Knowledge read ACL parity when optional roles are omitted."""
    from infra.python.catalog_role_policy import CATALOG_WRITE_TABLES
    from infra.python.knowledge_index_role_policy import KNOWLEDGE_INDEX_RUNTIME_READ_TABLES
    from infra.python.provision_database_roles import provision_roles

    admin = database
    suffix = uuid4().hex[:12]
    runtime, writer, unrelated = (f"parity_{part}_{suffix}" for part in ("runtime", "writer", "unrelated"))
    password = f"synthetic-{suffix}-only"
    reader = create_async_engine(admin.url.set(username=runtime, password=password))

    expected_runtime = {
        table: {
            "SELECT": True,
            "INSERT": False,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
            "REFERENCES": False,
            "TRIGGER": False,
        }
        for table in (CATALOG_WRITE_TABLES | KNOWLEDGE_INDEX_RUNTIME_READ_TABLES)
    }
    expected_none = {
        table: {
            "SELECT": False,
            "INSERT": False,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
            "REFERENCES": False,
            "TRIGGER": False,
        }
        for table in (CATALOG_WRITE_TABLES | KNOWLEDGE_INDEX_RUNTIME_READ_TABLES)
    }

    try:
        async with admin.begin() as connection:
            for role in (runtime, writer, unrelated):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            await provision_roles(
                connection,
                owner=config.DB_USER,
                runtime=runtime,
                writer=writer,
                catalog_writer=None,
                knowledge_index_builder=None,
            )

        # 1: Runtime has SELECT only across all Catalog & Knowledge tables
        await _assert_runtime_table_privileges(admin, runtime, expected_runtime)

        # 2: PUBLIC has no privileges across Catalog & Knowledge tables
        await _assert_runtime_table_privileges(admin, "public", expected_none)

        # 3: Unrelated role has no privileges across Catalog & Knowledge tables
        await _assert_runtime_table_privileges(admin, unrelated, expected_none)

        # 4: Runtime read queries succeed, write/modification queries fail with 42501
        async with reader.begin() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM rag_catalog_set")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM rag_knowledge_index")) == 0

        for stmt in (
            "INSERT INTO rag_catalog_set (catalog_version) VALUES ('v1')",
            "UPDATE rag_catalog_set SET catalog_version = 'v2'",
            "DELETE FROM rag_catalog_set",
            "TRUNCATE rag_catalog_set",
            "INSERT INTO rag_knowledge_index (index_code) VALUES ('IDX')",
            "UPDATE rag_knowledge_index SET index_code = 'IDX2'",
            "DELETE FROM rag_knowledge_index",
            "TRUNCATE rag_knowledge_index",
        ):
            with pytest.raises(DBAPIError) as error:
                async with reader.begin() as conn:
                    await conn.execute(text(stmt))
            assert error.value.orig.sqlstate == "42501"

    finally:
        await reader.dispose()
        async with admin.begin() as connection:
            for role in (runtime, writer, unrelated):
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


async def test_runtime_candidate_index_read_acl_parity_and_lock_marker(database) -> None:
    """#780: Verify Runtime Candidate Index read ACL parity, lock marker column, and FOR SHARE execution."""
    from app.models.rag_candidate_index import (
        RagCandidateIndexBuildMode,
        RagCandidateIndexEntityType,
        RagCandidateIndexMember,
        RagCandidateIndexStatus,
        RagCandidateIndexVersion,
    )
    from app.models.rag_catalog import (
        RagCatalogSet,
        RagCatalogSetSource,
        RagMedicationSearchEntryType,
    )
    from app.repositories.rag_candidate_index_repository import (
        RagCandidateIndexMemberCreate,
        RagCandidateIndexRepository,
        _recomputed_lexical_member_content_hash,
        _sha256,
    )
    from app.repositories.rag_source_catalog_repository import (
        RagSourceCatalogRepository,
        RagSourceCreate,
        RagSourceEndpointCreate,
        RagSourceOperationCreate,
        RagSourceSnapshotCreate,
    )
    from infra.python.provision_database_roles import provision_roles

    admin = database
    suffix = uuid4().hex[:12]
    runtime, writer, unrelated = (f"candi_{part}_{suffix}" for part in ("runtime", "writer", "unrelated"))
    password = f"synthetic-{suffix}-only"
    reader = create_async_engine(admin.url.set(username=runtime, password=password))

    candidate_tables = {
        "rag_candidate_index_version",
        "rag_candidate_index_member",
    }
    expected_runtime = {
        table: {
            "SELECT": True,
            "INSERT": False,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
            "REFERENCES": False,
            "TRIGGER": False,
        }
        for table in candidate_tables
    }
    expected_none = {
        table: {
            "SELECT": False,
            "INSERT": False,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
            "REFERENCES": False,
            "TRIGGER": False,
        }
        for table in candidate_tables
    }

    try:
        async with admin.begin() as connection:
            for role in (runtime, writer, unrelated):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            await provision_roles(
                connection,
                owner=config.DB_USER,
                runtime=runtime,
                writer=writer,
                catalog_writer=None,
                knowledge_index_builder=None,
            )

        # 1: Table privileges: Runtime has SELECT only across Candidate Index tables
        await _assert_runtime_table_privileges(admin, runtime, expected_runtime)

        # 2: Table privileges: PUBLIC and unrelated roles have NO privileges
        await _assert_runtime_table_privileges(admin, "public", expected_none)
        await _assert_runtime_table_privileges(admin, unrelated, expected_none)

        # 3: Column privileges: candidate_index_lock_marker UPDATE only
        async with admin.connect() as connection:
            has_marker_update = await connection.scalar(
                text(
                    "SELECT has_column_privilege(:role, 'rag_candidate_index_version', 'candidate_index_lock_marker', 'UPDATE')"
                ),
                {"role": runtime},
            )
            assert has_marker_update is True
            for col in ("index_code", "index_version", "status", "content_hash", "catalog_version"):
                col_update = await connection.scalar(
                    text("SELECT has_column_privilege(:role, 'rag_candidate_index_version', :col, 'UPDATE')"),
                    {"role": runtime, "col": col},
                )
                assert col_update is False

        # 4: Direct SQL execution privileges on rag_candidate_index_version
        async with reader.begin() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM rag_candidate_index_version")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM rag_candidate_index_member")) == 0

        # Runtime UPDATE marker = 0 succeeds (permission granted)
        async with reader.begin() as conn:
            await conn.execute(
                text("UPDATE rag_candidate_index_version SET candidate_index_lock_marker = 0 WHERE false")
            )

        for stmt in (
            "INSERT INTO rag_candidate_index_version (index_code) VALUES ('fail')",
            "UPDATE rag_candidate_index_version SET status = 'READY'",
            "UPDATE rag_candidate_index_version SET content_hash = 'fail'",
            "DELETE FROM rag_candidate_index_version",
            "TRUNCATE rag_candidate_index_version",
            "INSERT INTO rag_candidate_index_member (member_key) VALUES ('fail')",
            "UPDATE rag_candidate_index_member SET display_text = 'fail'",
            "DELETE FROM rag_candidate_index_member",
            "TRUNCATE rag_candidate_index_member",
        ):
            with pytest.raises(DBAPIError) as error:
                async with reader.begin() as conn:
                    await conn.execute(text(stmt))
            assert error.value.orig.sqlstate == "42501"

        # 5: Seed valid READY candidate index and verify get_verified_ready_index_snapshot under runtime role
        index_code = f"IDX_{suffix}"
        index_version = "v1"
        async with async_sessionmaker(admin, expire_on_commit=False)() as session:
            repo = RagSourceCatalogRepository(session)
            source = await repo.create_source(
                RagSourceCreate(source_code=f"SRC_{suffix}", display_name="Src", owner_name="Owner")
            )
            endpoint = await repo.create_endpoint(
                RagSourceEndpointCreate(source_id=source.id, endpoint_code="EP", display_name="EP")
            )
            op = await repo.create_operation(
                RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="OP", display_name="OP")
            )
            snapshot = await repo.create_snapshot(
                RagSourceSnapshotCreate(
                    operation_id=op.id,
                    source_version="v1",
                    raw_manifest_checksum="a" * 64,
                    canonical_checksum="b" * 64,
                    schema_version="schema-v1",
                    parser_version="parser-v1",
                    normalization_version="norm-v1",
                    canonicalization_spec_version="canon-v1",
                    record_count=1,
                    rejected_record_count=0,
                    collected_at=datetime.now(UTC),
                )
            )
            cat_set = RagCatalogSet(
                catalog_version="cat-v1",
                schema_version="schema-v1",
                normalization_version="norm-v1",
                manifest_spec_version=f"spec-{suffix}",
                envelope_hash="c" * 64,
                manifest_json=b"{}",
            )
            session.add(cat_set)
            await session.flush()

            cat_source = RagCatalogSetSource(
                set_id=cat_set.id,
                source_snapshot_id=snapshot.id,
                source_version=snapshot.source_version,
            )
            session.add(cat_source)
            await session.flush()

            member_payload = RagCandidateIndexMemberCreate(
                entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
                identity_entity_type=RagCandidateIndexEntityType.PRODUCT,
                identity_code_system="MFDS",
                identity_canonical_code=f"CANON_{suffix}",
                product_ref=f"prod:{suffix}",
                entry_ref=f"entry:{suffix}",
                display_text="Test Med",
                normalized_text="test med",
                product_name="Test Med",
                product_source_snapshot_id=snapshot.id,
                entry_source_snapshot_id=snapshot.id,
                catalog_version=cat_set.catalog_version,
                catalog_manifest_hash=cat_set.envelope_hash,
                normalization_version=cat_set.normalization_version,
                member_key=f"MK_{suffix}",
                member_content_hash="",
            )
            member_content_hash = _recomputed_lexical_member_content_hash(member_payload)
            member_set_hash = _sha256(
                [{"member_key": member_payload.member_key, "member_content_hash": member_content_hash}]
            )
            content_hash = _sha256({"member_set_hash": member_set_hash, "version": "v1"})

            version_row = RagCandidateIndexVersion(
                index_code=index_code,
                index_version=index_version,
                status=RagCandidateIndexStatus.READY,
                build_mode=RagCandidateIndexBuildMode.LEXICAL_ONLY,
                catalog_set_id=cat_set.id,
                catalog_version=cat_set.catalog_version,
                catalog_manifest_hash=cat_set.envelope_hash,
                schema_version=cat_set.schema_version,
                normalization_version=cat_set.normalization_version,
                lexical_config_version="lex-v1",
                search_order_version="order-v1",
                candidate_limit=10,
                display_limit=5,
                member_count=1,
                product_identity_count=1,
                product_name_count=1,
                approved_alias_count=0,
                vector_count=0,
                member_set_hash=member_set_hash,
                configuration_hash="d" * 64,
                content_hash=content_hash,
            )
            session.add(version_row)
            await session.flush()

            member_row = RagCandidateIndexMember(
                candidate_index_version_id=version_row.id,
                entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
                identity_entity_type=RagCandidateIndexEntityType.PRODUCT,
                identity_code_system="MFDS",
                identity_canonical_code=f"CANON_{suffix}",
                product_ref=f"prod:{suffix}",
                entry_ref=f"entry:{suffix}",
                display_text="Test Med",
                normalized_text="test med",
                product_name="Test Med",
                product_source_snapshot_id=snapshot.id,
                entry_source_snapshot_id=snapshot.id,
                catalog_version=cat_set.catalog_version,
                catalog_manifest_hash=cat_set.envelope_hash,
                normalization_version=cat_set.normalization_version,
                member_key=f"MK_{suffix}",
                member_content_hash=member_content_hash,
                lexical_storage_hash=member_content_hash,
            )
            session.add(member_row)
            await session.commit()

        # Execute get_verified_ready_index_snapshot with reader session (runtime role)
        # Verifies that SELECT ... FOR SHARE succeeds under Runtime privileges.
        async with async_sessionmaker(reader, expire_on_commit=False)() as session:
            runtime_repo = RagCandidateIndexRepository(session)
            verified = await runtime_repo.get_verified_ready_index_snapshot(
                index_code=index_code,
                expected_index_version=index_version,
            )
            assert verified.version.index_code == index_code
            assert len(verified.members) == 1

        # Check constraint test on marker: updating to non-zero must fail with check_violation
        with pytest.raises(DBAPIError) as error:
            async with reader.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE rag_candidate_index_version SET candidate_index_lock_marker = 1 WHERE index_code = :code"
                    ),
                    {"code": index_code},
                )
        assert error.value.orig.sqlstate == "23514"

    finally:
        await reader.dispose()
        async with admin.begin() as connection:
            for role in (runtime, writer, unrelated):
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


async def test_candidate_index_builder_least_privilege_and_execution_boundary(database) -> None:
    """Issue #800: Verify least-privilege boundary, approval access, single-transaction lifecycle, and #780 invariance."""
    from ai_worker.admin.catalog_writer import validate_catalog_writer
    from app.models.rag_candidate_index import (
        RagCandidateIndexBuildMode,
        RagCandidateIndexEntityType,
        RagCandidateIndexStatus,
    )
    from app.models.rag_catalog import (
        RagCatalogSet,
        RagCatalogSetSource,
        RagMedicationSearchEntryType,
    )
    from app.repositories.rag_candidate_index_repository import (
        RagCandidateIndexMemberCreate,
        RagCandidateIndexRepository,
        RagCandidateIndexVersionCreate,
        _recomputed_lexical_member_content_hash,
        _sha256,
    )
    from app.repositories.rag_source_catalog_repository import (
        RagSourceCatalogRepository,
        RagSourceCreate,
        RagSourceEndpointCreate,
        RagSourceOperationCreate,
        RagSourceSnapshotCreate,
    )
    from infra.python.catalog_role_policy import CATALOG_APPROVAL_READ_TABLES
    from infra.python.provision_database_roles import provision_roles
    from scripts.candidate_index_builder import validate_candidate_index_builder

    admin = database
    suffix = uuid4().hex[:12]
    runtime, writer, cat_writer, cand_builder, unrelated = (
        f"bnd_{part}_{suffix}" for part in ("runtime", "writer", "catw", "candb", "unrel")
    )
    password = f"synthetic-{suffix}-only"

    cat_engine = create_async_engine(admin.url.set(username=cat_writer, password=password))
    builder_engine = create_async_engine(admin.url.set(username=cand_builder, password=password))
    reader_engine = create_async_engine(admin.url.set(username=runtime, password=password))

    try:
        # 1. Provision roles with Catalog Writer and Candidate Builder
        async with admin.begin() as connection:
            for role in (runtime, writer, cat_writer, cand_builder, unrelated):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            await provision_roles(
                connection,
                owner=config.DB_USER,
                runtime=runtime,
                writer=writer,
                catalog_writer=cat_writer,
                candidate_index_builder=cand_builder,
            )

        # 2. Validate Catalog Writer connection and approval read grants
        async with cat_engine.connect() as conn:
            await validate_catalog_writer(conn)
            for table in sorted(CATALOG_APPROVAL_READ_TABLES):
                count = await conn.scalar(text(f"SELECT count(*) FROM public.{table}"))
                assert count == 0

        # Catalog Writer cannot insert into approval tables
        with pytest.raises(DBAPIError) as error:
            async with cat_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO public.catalog_source_approval (id) VALUES ('00000000-0000-0000-0000-000000000001'::uuid)"
                    )
                )
        assert error.value.orig.sqlstate == "42501"

        # 3. Validate Candidate Index Builder connection least-privilege boundary
        async with builder_engine.connect() as conn:
            await validate_candidate_index_builder(conn, expected_user=cand_builder)

        # 4. Table and Column Privilege enforcement for Candidate Builder
        async with builder_engine.begin() as conn:
            # SELECT on candidate tables and catalog tables succeeds
            assert await conn.scalar(text("SELECT count(*) FROM rag_candidate_index_version")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM rag_candidate_index_member")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM rag_catalog_set")) == 0

        # UPDATE candidate_index_lock_marker is PROHIBITED for Candidate Builder (reserved for Runtime Reader #780)
        with pytest.raises(DBAPIError) as error:
            async with builder_engine.begin() as conn:
                await conn.execute(
                    text("UPDATE rag_candidate_index_version SET candidate_index_lock_marker = 0 WHERE false")
                )
        assert error.value.orig.sqlstate == "42501"

        # Direct UPDATE on provenance columns is PROHIBITED for Candidate Builder
        for col, val in (
            ("content_hash", "'hack'"),
            ("configuration_hash", "'hack'"),
            ("member_count", "123"),
            ("index_code", "'hack'"),
            ("index_version", "'hack'"),
        ):
            with pytest.raises(DBAPIError) as error:
                async with builder_engine.begin() as conn:
                    await conn.execute(text(f"UPDATE rag_candidate_index_version SET {col} = {val} WHERE false"))
            assert error.value.orig.sqlstate == "42501"

        # DELETE and TRUNCATE are PROHIBITED for Candidate Builder
        for stmt in (
            "DELETE FROM rag_candidate_index_version",
            "TRUNCATE rag_candidate_index_version",
            "DELETE FROM rag_candidate_index_member",
            "TRUNCATE rag_candidate_index_member",
            "CREATE TABLE hack (id int)",
        ):
            with pytest.raises(DBAPIError) as error:
                async with builder_engine.begin() as conn:
                    await conn.execute(text(stmt))
            assert error.value.orig.sqlstate == "42501"

        # 5. Candidate Builder execution: single-transaction build, promotion, and readback
        index_code = f"IDX_{suffix}"
        index_version = "v1"

        # Seed authoritative Catalog Set and Source Snapshot
        async with async_sessionmaker(admin, expire_on_commit=False)() as session:
            repo = RagSourceCatalogRepository(session)
            source = await repo.create_source(
                RagSourceCreate(source_code=f"SRC_{suffix}", display_name="Src", owner_name="Owner")
            )
            endpoint = await repo.create_endpoint(
                RagSourceEndpointCreate(source_id=source.id, endpoint_code="EP", display_name="EP")
            )
            op = await repo.create_operation(
                RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="OP", display_name="OP")
            )
            snapshot = await repo.create_snapshot(
                RagSourceSnapshotCreate(
                    operation_id=op.id,
                    source_version="v1",
                    raw_manifest_checksum="a" * 64,
                    canonical_checksum="b" * 64,
                    schema_version="schema-v1",
                    parser_version="parser-v1",
                    normalization_version="norm-v1",
                    canonicalization_spec_version="canon-v1",
                    record_count=1,
                    rejected_record_count=0,
                    collected_at=datetime.now(UTC),
                )
            )
            cat_set = RagCatalogSet(
                catalog_version="cat-v1",
                schema_version="schema-v1",
                normalization_version="norm-v1",
                manifest_spec_version=f"spec-{suffix}",
                envelope_hash="e" * 64,
                manifest_json=b"{}",
            )
            session.add(cat_set)
            await session.flush()

            cat_source = RagCatalogSetSource(
                set_id=cat_set.id,
                source_snapshot_id=snapshot.id,
                source_version=snapshot.source_version,
            )
            session.add(cat_source)
            await session.commit()

        member_payload = RagCandidateIndexMemberCreate(
            entry_type=RagMedicationSearchEntryType.PRODUCT_NAME,
            identity_entity_type=RagCandidateIndexEntityType.PRODUCT,
            identity_code_system="MFDS",
            identity_canonical_code=f"CANON_{suffix}",
            product_ref=f"prod:{suffix}",
            entry_ref=f"entry:{suffix}",
            display_text="Test Med",
            normalized_text="test med",
            product_name="Test Med",
            product_source_snapshot_id=snapshot.id,
            entry_source_snapshot_id=snapshot.id,
            catalog_version=cat_set.catalog_version,
            catalog_manifest_hash=cat_set.envelope_hash,
            normalization_version=cat_set.normalization_version,
            member_key=f"MK_{suffix}",
            member_content_hash="",
        )
        computed_hash = _recomputed_lexical_member_content_hash(member_payload)
        import dataclasses

        member_payload = dataclasses.replace(member_payload, member_content_hash=computed_hash)
        member_set_hash = _sha256([{"member_key": member_payload.member_key, "member_content_hash": computed_hash}])
        content_hash = _sha256({"member_set_hash": member_set_hash, "version": "v1"})

        version_payload = RagCandidateIndexVersionCreate(
            index_code=index_code,
            index_version=index_version,
            build_mode=RagCandidateIndexBuildMode.LEXICAL_ONLY,
            catalog_set_id=cat_set.id,
            catalog_version=cat_set.catalog_version,
            catalog_manifest_hash=cat_set.envelope_hash,
            schema_version=cat_set.schema_version,
            normalization_version=cat_set.normalization_version,
            lexical_config_version="lex-v1",
            search_order_version="order-v1",
            candidate_limit=10,
            display_limit=5,
            member_count=1,
            product_identity_count=1,
            product_name_count=1,
            approved_alias_count=0,
            vector_count=0,
            member_set_hash=member_set_hash,
            configuration_hash="f" * 64,
            content_hash=content_hash,
        )

        # Candidate Builder creates version + member and activates version in single transaction
        builder_session_maker = async_sessionmaker(builder_engine, expire_on_commit=False)
        async with builder_session_maker() as session:
            async with session.begin():
                builder_repo = RagCandidateIndexRepository(session)
                built = await builder_repo.build_index_version(
                    version=version_payload,
                    members=(member_payload,),
                )
                assert built.version.status is RagCandidateIndexStatus.BUILDING
                # Promotion to READY succeeds under Candidate Builder UPDATE(status) privilege
                activated = await builder_repo.activate_ready_version(built.version.id)
                assert activated.status is RagCandidateIndexStatus.READY

        # Post-commit verification succeeds under Candidate Builder SELECT privilege
        async with builder_session_maker() as session:
            builder_repo = RagCandidateIndexRepository(session)
            verified = await builder_repo.get_verified_ready_index_snapshot(
                index_code=index_code,
                expected_index_version=index_version,
            )
            assert verified.version.status is RagCandidateIndexStatus.READY
            assert len(verified.members) == 1

        # 6. Runtime #780 Invariance: Verify Runtime Reader permissions remain strictly intact
        async with reader_engine.begin() as conn:
            # Runtime SELECT on version and member succeeds
            assert await conn.scalar(text("SELECT count(*) FROM rag_candidate_index_version")) == 1
            assert await conn.scalar(text("SELECT count(*) FROM rag_candidate_index_member")) == 1
            # Runtime UPDATE on candidate_index_lock_marker succeeds
            await conn.execute(
                text("UPDATE rag_candidate_index_version SET candidate_index_lock_marker = 0 WHERE false")
            )

        # Runtime UPDATE(status) is PROHIBITED
        with pytest.raises(DBAPIError) as error:
            async with reader_engine.begin() as conn:
                await conn.execute(text("UPDATE rag_candidate_index_version SET status = 'BUILDING'"))
        assert error.value.orig.sqlstate == "42501"

        # Runtime INSERT/DELETE is PROHIBITED
        for stmt in (
            "INSERT INTO rag_candidate_index_version (index_code) VALUES ('fail')",
            "DELETE FROM rag_candidate_index_version",
        ):
            with pytest.raises(DBAPIError) as error:
                async with reader_engine.begin() as conn:
                    await conn.execute(text(stmt))
            assert error.value.orig.sqlstate == "42501"

    finally:
        await cat_engine.dispose()
        await builder_engine.dispose()
        await reader_engine.dispose()
        async with admin.begin() as connection:
            for role in (runtime, writer, cat_writer, cand_builder, unrelated):
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


async def _assert_column_privileges(connection, *, table: str, columns: tuple[str, ...], privilege: str) -> None:
    for column in columns:
        assert await connection.scalar(
            text("SELECT has_column_privilege(current_user, :table, :column, :privilege)"),
            {"table": table, "column": column, "privilege": privilege},
        )


async def _assert_statements_forbidden(engine, statements: tuple[str, ...]) -> None:
    for statement in statements:
        with pytest.raises(DBAPIError) as error:
            async with engine.begin() as connection:
                await connection.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"


async def test_catalog_approval_role_least_privilege_and_boundary(database) -> None:
    """#526 Phase 2: Verify Catalog approval role least-privilege boundary and writer separation."""
    from ai_worker.admin.catalog_approval import ApprovalRequest, fingerprint, set_permission
    from ai_worker.admin.catalog_writer import validate_catalog_writer
    from infra.python.catalog_approval_role_policy import (
        APPROVAL_INSERT_TABLES,
        APPROVAL_READ_TABLES,
        APPROVAL_SOURCE_READ_TABLES,
        APPROVAL_STATE_INSERT_TABLES,
        REVOCABLE_TABLES,
        validate_catalog_approval_connection,
    )
    from infra.python.catalog_role_policy import CATALOG_APPROVAL_READ_TABLES
    from infra.python.provision_database_roles import provision_roles

    admin = database
    suffix = uuid4().hex[:12]
    runtime, writer, cat_writer, appr_role, unrelated = (
        f"bnd_{part}_{suffix}" for part in ("runtime", "writer", "catw", "appr", "unrel")
    )
    password = f"synthetic-{suffix}-only"

    cat_engine = create_async_engine(admin.url.set(username=cat_writer, password=password))
    appr_engine = create_async_engine(admin.url.set(username=appr_role, password=password))
    reader_engine = create_async_engine(admin.url.set(username=runtime, password=password))

    try:
        # 1. Provision roles with Catalog Writer and Catalog Approval
        async with admin.begin() as connection:
            for role in (runtime, writer, cat_writer, appr_role, unrelated):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            await provision_roles(
                connection,
                owner=config.DB_USER,
                runtime=runtime,
                writer=writer,
                catalog_writer=cat_writer,
                catalog_approval=appr_role,
            )

        # Empty permission state must be bootstrap-capable through the dedicated approval DB principal.
        actor_id = uuid4()
        request_id = uuid4()
        evidence_ref = "synthetic://catalog-approval-bootstrap"
        async with admin.begin() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM catalog_approval_permission")) == 0
            await connection.execute(
                text(
                    'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                    "VALUES (:id, :email, 'synthetic-hash', '합성 승인자', true, false)"
                ),
                {"id": str(actor_id), "email": f"appr-{uuid4().hex[:8]}@example.test"},
            )

        request = ApprovalRequest(actor_id=actor_id, request_id=request_id, evidence_ref=evidence_ref)
        approval_sessions = async_sessionmaker(appr_engine, expire_on_commit=False)
        async with approval_sessions.begin() as session:
            result = await set_permission(session, request=request, user_id=actor_id, enabled=True)
        assert result["status"] == "APPLIED"

        expected_fingerprint = fingerprint(
            {
                "actor_id": str(actor_id),
                "request_id": str(request_id),
                "evidence_ref": evidence_ref,
                "user_id": str(actor_id),
                "enabled": True,
                "event_kind": "GRANT_PERMISSION",
            }
        )
        async with admin.connect() as connection:
            permission = (
                await connection.execute(
                    text(
                        "SELECT user_id, enabled, evidence_ref, revision "
                        "FROM catalog_approval_permission WHERE user_id=:user_id"
                    ),
                    {"user_id": str(actor_id)},
                )
            ).one()
            assert permission == (str(actor_id), True, evidence_ref, 1)
            audit = (
                await connection.execute(
                    text(
                        "SELECT event_kind, actor_id, subject_user_id, request_id, evidence_ref, request_fingerprint "
                        "FROM catalog_approval_audit"
                    )
                )
            ).one()
            assert audit == (
                "GRANT_PERMISSION",
                str(actor_id),
                str(actor_id),
                str(request_id),
                evidence_ref,
                expected_fingerprint,
            )

        # 2. Validate Approval role connection
        async with appr_engine.connect() as conn:
            await validate_catalog_approval_connection(conn)

            await _assert_column_privileges(
                conn,
                table="catalog_approval_permission",
                columns=("user_id", "enabled", "evidence_ref", "revision", "updated_at"),
                privilege="INSERT",
            )
            await _assert_column_privileges(
                conn,
                table="catalog_approval_permission",
                columns=("enabled", "evidence_ref", "revision", "updated_at"),
                privilege="UPDATE",
            )
            assert not await conn.scalar(
                text("SELECT has_column_privilege(current_user, 'catalog_approval_permission', 'user_id', 'UPDATE')")
            )

            # Approval role can SELECT approval and source tables
            for table in sorted(APPROVAL_READ_TABLES | APPROVAL_SOURCE_READ_TABLES):
                count = await conn.scalar(text(f"SELECT count(*) FROM public.{table}"))
                assert count >= 0

            # Approval role can SELECT allowed user columns
            user_row = await conn.execute(text('SELECT id, is_active, account_status FROM public."user" LIMIT 1'))
            assert user_row is not None

        revoke_request = ApprovalRequest(
            actor_id=actor_id,
            request_id=uuid4(),
            evidence_ref="synthetic://catalog-approval-bootstrap-revoke",
        )
        async with approval_sessions.begin() as session:
            updated = await set_permission(session, request=revoke_request, user_id=actor_id, enabled=False)
        assert updated["status"] == "APPLIED"
        assert updated["revision"] == 2
        async with admin.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT enabled, evidence_ref, revision FROM catalog_approval_permission WHERE user_id=:user_id"
                    ),
                    {"user_id": str(actor_id)},
                )
            ).one() == (False, revoke_request.evidence_ref, 2)
            assert await connection.scalar(text("SELECT count(*) FROM catalog_approval_audit")) == 2

        # Approval role CANNOT SELECT user sensitive columns (e.g. hashed_password)
        with pytest.raises(DBAPIError) as error:
            async with appr_engine.connect() as conn:
                await conn.execute(text('SELECT hashed_password FROM public."user" LIMIT 1'))
        assert error.value.orig.sqlstate == "42501"

        # Approval role can UPDATE revocable columns
        async with appr_engine.begin() as conn:
            for table in sorted(REVOCABLE_TABLES):
                await conn.execute(
                    text(f"UPDATE public.{table} SET revoked_at = NOW(), revoked_reason = 'test' WHERE false")
                )

        # Approval role CANNOT UPDATE non-revocable columns on approval tables
        with pytest.raises(DBAPIError) as error:
            async with appr_engine.begin() as conn:
                await conn.execute(
                    text("UPDATE public.catalog_build_approval SET catalog_version = 'forbidden' WHERE false")
                )
        assert error.value.orig.sqlstate == "42501"

        # Approval role CANNOT DELETE/TRUNCATE approval state or append-only audit.
        forbidden_state_changes = tuple(
            operation
            for table in sorted(APPROVAL_INSERT_TABLES | APPROVAL_STATE_INSERT_TABLES)
            for operation in (f"DELETE FROM public.{table} WHERE false", f"TRUNCATE public.{table}")
        )
        await _assert_statements_forbidden(
            appr_engine,
            forbidden_state_changes
            + (
                "UPDATE public.catalog_approval_audit SET event_kind = event_kind WHERE false",
                "UPDATE public.catalog_approval_permission SET user_id = user_id WHERE false",
                "CREATE TABLE public.catalog_approval_forbidden (id integer)",
            ),
        )

        # Approval role CANNOT write to Catalog business tables
        for stmt in (
            "INSERT INTO public.rag_medication_product (id) VALUES ('00000000-0000-0000-0000-000000000001'::uuid)",
            "UPDATE public.rag_medication_product SET product_name = 'fail' WHERE false",
            "DELETE FROM public.rag_medication_product WHERE false",
        ):
            with pytest.raises(DBAPIError) as error:
                async with appr_engine.begin() as conn:
                    await conn.execute(text(stmt))
            assert error.value.orig.sqlstate == "42501"

        # 3. Validate Catalog Writer: can read approval tables, CANNOT write to approval tables
        async with cat_engine.connect() as conn:
            await validate_catalog_writer(conn)
            for table in sorted(CATALOG_APPROVAL_READ_TABLES):
                count = await conn.scalar(text(f"SELECT count(*) FROM public.{table}"))
                assert count >= 0

        # Catalog Writer CANNOT insert, update, or delete on approval tables
        for table in sorted(APPROVAL_INSERT_TABLES | APPROVAL_STATE_INSERT_TABLES):
            with pytest.raises(DBAPIError) as error:
                async with cat_engine.begin() as conn:
                    await conn.execute(text(f"INSERT INTO public.{table} DEFAULT VALUES"))
            assert error.value.orig.sqlstate == "42501"

        with pytest.raises(DBAPIError) as error:
            async with cat_engine.begin() as conn:
                await conn.execute(
                    text("UPDATE public.catalog_build_approval SET catalog_version = 'forbidden' WHERE false")
                )
        assert error.value.orig.sqlstate == "42501"

        with pytest.raises(DBAPIError) as error:
            async with cat_engine.begin() as conn:
                await conn.execute(text("DELETE FROM public.catalog_build_approval WHERE false"))
        assert error.value.orig.sqlstate == "42501"

        # 4. Validate Runtime: can read approval tables (Phase 2 requirement for load_build verification)
        async with reader_engine.connect() as conn:
            for table in sorted(CATALOG_APPROVAL_READ_TABLES):
                count = await conn.scalar(text(f"SELECT count(*) FROM public.{table}"))
                assert count >= 0

    finally:
        await cat_engine.dispose()
        await appr_engine.dispose()
        await reader_engine.dispose()
        async with admin.begin() as connection:
            for role in (runtime, writer, cat_writer, appr_role, unrelated):
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
