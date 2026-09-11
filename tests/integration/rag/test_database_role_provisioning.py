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
from infra.python.provision_database_roles import (
    RUNTIME_APPEND_ONLY_TABLES,
    RUNTIME_AUTH_UPDATE_COLUMNS,
    RUNTIME_MUTABLE_TABLES,
    run_provisioning,
)
from infra.python.source_management_role_policy import CATALOG_TABLES
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
            for table in sorted(
                RUNTIME_MUTABLE_TABLES
                | RUNTIME_APPEND_ONLY_TABLES
                | CATALOG_TABLES
                | set(SOURCE_TABLES)
                | set(RUNTIME_AUTH_UPDATE_COLUMNS)
            ):
                await connection.execute(text(f'CREATE TABLE "{table}" (id integer PRIMARY KEY)'))
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
            (producer, "UPDATE rag_source_ingestion_run SET attempted_source_version=NULL"),
            (reader, "UPDATE rag_source_ingestion_run SET run_status=NULL"),
            (reader, "UPDATE checkin_audit SET id=2"),
            (reader, "DELETE FROM checkin_audit"),
            (reader, "UPDATE rag_medication_product SET id=2"),
            (reader, "DELETE FROM rag_medication_alias"),
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

    await _exercise_prescription_candidate_cutover(admin, reader, environment)
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
                "VALUES (:id,'synthetic-audit','ACTIVE')"
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
    concurrent_env_id = uuid4()
    async with admin.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_runtime_environment (id,environment_code,environment_status) "
                "VALUES (:id,:code,'ACTIVE')"
            ),
            {"id": str(concurrent_env_id), "code": f"synthetic-concurrent-{concurrent_env_id.hex[:8]}"},
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


async def _exercise_prescription_candidate_cutover(admin, reader, environment):
    from app.models.rag_candidate import MedicationCandidateSearchStatus
    from app.repositories.medication_candidate_repository import MedicationCandidateRepository
    from app.repositories.prescription_repository import PrescriptionRepository
    from app.tests.rag.test_medication_candidate_repository import _ready_result
    from app.tests.repositories.test_medication_schedule_repository_integration import (
        _create_active_version_medication,
        _create_user_with_self_profile,
    )

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
        for tables, privileges in (
            (RUNTIME_MUTABLE_TABLES, "SELECT, INSERT, UPDATE, DELETE"),
            (RUNTIME_APPEND_ONLY_TABLES | CATALOG_TABLES, "SELECT, INSERT"),
        ):
            for table in tables - {
                "ai_job_intake_context",
                "ai_job_execution_context",
                "ai_job_execution_identification",
            }:
                await connection.execute(text(f'GRANT {privileges} ON "{table}" TO "{runtime}"'))
        for table in SOURCE_TABLES:
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
