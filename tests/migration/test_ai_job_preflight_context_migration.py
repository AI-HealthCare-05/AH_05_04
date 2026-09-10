from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_CONTEXT_REVISION = "174a1b2c3d4e"
PREFLIGHT_CONTEXT_BASE_REVISION = "206a1b2c3d4e"
PREFLIGHT_CONTEXT_TABLES = {
    "ai_job_intake_context",
    "ai_job_execution_context",
    "ai_job_execution_identification",
}


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


async def _fetch_table_names() -> set[str]:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(:table_names)
                """
            ),
            {"table_names": list(PREFLIGHT_CONTEXT_TABLES)},
        )
        return {row[0] for row in result}


async def _fetch_schema_object_names() -> set[str]:
    async with _connection() as connection:
        constraints = await connection.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = ANY(:table_names)
                """
            ),
            {"table_names": list(PREFLIGHT_CONTEXT_TABLES | {"medication_identification"})},
        )
        indexes = await connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = ANY(:table_names)
                """
            ),
            {"table_names": list(PREFLIGHT_CONTEXT_TABLES | {"medication_identification"})},
        )
        return {*(row[0] for row in constraints), *(row[0] for row in indexes)}


async def _seed_preflight_context_graph() -> dict[str, str]:
    ids = {
        key: str(uuid4())
        for key in (
            "user_id",
            "profile_id",
            "document_id",
            "ocr_job_id",
            "prescription_id",
            "version_id",
            "version_medication_id",
            "ai_job_id",
            "chat_session_id",
            "chat_message_id",
            "manifest_id",
            "bundle_id",
            "environment_id",
            "search_id",
            "result_id",
            "identification_id",
            "intake_context_id",
            "execution_context_id",
            "execution_identification_id",
        )
    }
    suffix = uuid4().hex[:10]
    manifest_hash = (suffix + "a" * 64)[:64]
    bundle_hash = (suffix + "b" * 64)[:64]
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            await connection.execute(
                text(
                    """
                    INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin)
                    VALUES (:user_id, :email, 'synthetic-password-hash', 'preflight-test', true, false)
                    """
                ),
                {**ids, "email": f"preflight-{suffix}@test.local"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO profile (id, user_id, profile_type, display_name)
                    VALUES (:profile_id, :user_id, 'SELF', 'preflight-test')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medical_document (
                        id, uploaded_by, profile_id, document_type, original_file_name,
                        object_key, file_mime_type, file_size_bytes, upload_status
                    )
                    VALUES (
                        :document_id, :user_id, :profile_id, 'PRESCRIPTION', 'preflight.jpg',
                        :object_key, 'image/jpeg', 100, 'UPLOADED'
                    )
                    """
                ),
                {**ids, "object_key": f"synthetic/{suffix}.jpg"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ocr_job (id, document_id, ocr_status, completed_at)
                    VALUES (:ocr_job_id, :document_id, 'COMPLETED', now())
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription (
                        id, active_version_id, document_id, source_ocr_job_id, profile_id,
                        prescribed_date, prescription_status, confirmed_at
                    )
                    VALUES (
                        :prescription_id, :version_id, :document_id, :ocr_job_id, :profile_id,
                        DATE '2026-09-10', 'CONFIRMED', now()
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription_version (
                        id, prescription_id, version_number, prescribed_date, confirmed_at
                    )
                    VALUES (:version_id, :prescription_id, 1, DATE '2026-09-10', now())
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO prescription_version_medication (
                        id, prescription_version_id, medication_name, display_order
                    )
                    VALUES (:version_medication_id, :version_id, '합성 식별약', 1)
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ai_job (
                        id, user_id, job_type, status, prescription_version_id,
                        attempt_count, max_attempts
                    )
                    VALUES (:ai_job_id, :user_id, 'CHAT', 'PENDING', :version_id, 0, 2)
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO chat_session (
                        id, prescription_id, prescription_version_id, profile_id, session_status
                    )
                    VALUES (:chat_session_id, :prescription_id, :version_id, :profile_id, 'ACTIVE')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO chat_message (id, session_id, message_seq, role, content, generation_status)
                    VALUES (:chat_message_id, :chat_session_id, 1, 'USER', '합성 질문', 'NOT_APPLICABLE')
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_runtime_execution_manifest (
                        id, manifest_key, manifest_version, manifest_hash,
                        schema_version, git_commit_sha
                    )
                    VALUES (
                        :manifest_id, :manifest_key, '1.0.0', :manifest_hash,
                        'runtime-manifest-v1', 'abcdef1'
                    )
                    """
                ),
                {**ids, "manifest_key": f"preflight-runtime-{suffix}", "manifest_hash": manifest_hash},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_runtime_release_bundle (
                        id, bundle_key, bundle_version, bundle_status,
                        execution_manifest_id, bundle_manifest_hash
                    )
                    VALUES (
                        :bundle_id, :bundle_key, '1.0.0', 'READY',
                        :manifest_id, :bundle_hash
                    )
                    """
                ),
                {**ids, "bundle_key": f"preflight-bundle-{suffix}", "bundle_hash": bundle_hash},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_runtime_environment (
                        id, environment_code, environment_status,
                        active_bundle_id, active_bundle_manifest_hash,
                        environment_revision, safety_epoch
                    )
                    VALUES (
                        :environment_id, :environment_code, 'ACTIVE',
                        :bundle_id, :bundle_hash, 1, 1
                    )
                    """
                ),
                {**ids, "environment_code": f"preflight-{suffix}", "bundle_hash": bundle_hash},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medication_candidate_search (
                        id, prescription_version_medication_id, medication_name_snapshot, query_digest, status,
                        candidate_count, displayed_candidate_count
                    )
                    VALUES (:search_id, :version_medication_id, '합성 식별약', :query_digest, 'READY', 1, 1)
                    """
                ),
                {**ids, "query_digest": "c" * 64},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medication_candidate_search_result (
                        id, search_id, product_id, code_system, canonical_code,
                        product_name, product_status, result_rank, result_score, result_method,
                        is_displayed, selection_eligible
                    )
                    VALUES (
                        :result_id, :search_id, :product_id, 'MFDS_ITEM_SEQ', 'SYNTHETIC-001',
                        '합성 식별약 10mg', 'APPROVED', 1, 1.0, 'fixture-exact', true, true
                    )
                    """
                ),
                {**ids, "product_id": str(uuid4())},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO medication_identification (
                        id, prescription_version_medication_id, candidate_search_id,
                        candidate_search_result_id, product_id, code_system, canonical_code,
                        status, source, confirmed_at
                    )
                    SELECT
                        :identification_id, :version_medication_id, :search_id,
                        :result_id, product_id, code_system, canonical_code,
                        'MATCHED', 'USER_SELECTED', now()
                    FROM medication_candidate_search_result
                    WHERE id = :result_id
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ai_job_intake_context (
                        id, ai_job_id, chat_message_id, prescription_version_id,
                        runtime_environment_id, runtime_environment_revision,
                        runtime_release_bundle_id, runtime_release_bundle_manifest_hash,
                        runtime_execution_manifest_id, runtime_execution_manifest_hash,
                        runtime_guard_decision_ref, question_digest, patient_context_digest,
                        context_schema_version
                    )
                    VALUES (
                        :intake_context_id, :ai_job_id, :chat_message_id, :version_id,
                        :environment_id, 1, :bundle_id, :bundle_hash,
                        :manifest_id, :manifest_hash, 'guard:intake-pass',
                        :question_digest, :patient_digest, 'ai-job-intake-context@1'
                    )
                    """
                ),
                {
                    **ids,
                    "bundle_hash": bundle_hash,
                    "manifest_hash": manifest_hash,
                    "question_digest": "d" * 64,
                    "patient_digest": "e" * 64,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ai_job_execution_context (
                        id, ai_job_id, intake_context_id, chat_message_id, prescription_version_id,
                        runtime_environment_id, runtime_environment_revision,
                        runtime_release_bundle_id, runtime_release_bundle_manifest_hash,
                        runtime_execution_manifest_id, runtime_execution_manifest_hash,
                        runtime_guard_decision_ref, patient_context_digest,
                        source_scope_manifest_hash, context_schema_version
                    )
                    VALUES (
                        :execution_context_id, :ai_job_id, :intake_context_id,
                        :chat_message_id, :version_id, :environment_id, 1,
                        :bundle_id, :bundle_hash, :manifest_id, :manifest_hash,
                        'guard:full-pass', :patient_digest, :scope_hash,
                        'ai-job-execution-context@1'
                    )
                    """
                ),
                {
                    **ids,
                    "bundle_hash": bundle_hash,
                    "manifest_hash": manifest_hash,
                    "patient_digest": "e" * 64,
                    "scope_hash": "f" * 64,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO ai_job_execution_identification (
                        id, execution_context_id, medication_identification_id,
                        prescription_version_medication_id
                    )
                    VALUES (
                        :execution_identification_id, :execution_context_id,
                        :identification_id, :version_medication_id
                    )
                    """
                ),
                ids,
            )

    return ids


async def _cleanup_preflight_fixture_graph(ids: dict[str, str]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            for table_name, column_name, key in (
                ("ai_job_execution_identification", "id", "execution_identification_id"),
                ("ai_job_execution_context", "id", "execution_context_id"),
                ("ai_job_intake_context", "id", "intake_context_id"),
                ("medication_identification", "id", "identification_id"),
                ("medication_candidate_search_result", "id", "result_id"),
                ("medication_candidate_search", "id", "search_id"),
                ("chat_message", "id", "chat_message_id"),
                ("chat_session", "id", "chat_session_id"),
                ("ai_job", "id", "ai_job_id"),
                ("rag_runtime_environment", "id", "environment_id"),
                ("rag_runtime_release_bundle", "id", "bundle_id"),
                ("rag_runtime_execution_manifest", "id", "manifest_id"),
                ("prescription", "id", "prescription_id"),
                ("ocr_job", "id", "ocr_job_id"),
                ("medical_document", "id", "document_id"),
                ("profile", "id", "profile_id"),
                ("user", "id", "user_id"),
            ):
                table_exists = await connection.execute(
                    text("SELECT to_regclass(:table_name)"), {"table_name": table_name}
                )
                if table_exists.scalar_one() is not None:
                    await connection.execute(
                        text(f'DELETE FROM "{table_name}" WHERE "{column_name}" = :id'),
                        {"id": ids[key]},
                    )


async def _cleanup_context_tables() -> None:
    async with _connection() as connection:
        async with connection.begin():
            for table_name in (
                "ai_job_execution_identification",
                "ai_job_execution_context",
                "ai_job_intake_context",
            ):
                table_exists = await connection.execute(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = :table_name"
                        ")"
                    ),
                    {"table_name": table_name},
                )
                if table_exists.scalar_one():
                    await connection.execute(text(f'DELETE FROM "{table_name}"'))


@pytest.fixture(autouse=True)
def _isolated_preflight_database(monkeypatch) -> Iterator[None]:
    """Exercise historical downgrades without touching the irreversible #398 head."""
    database = f"preflight412_{uuid4().hex[:12]}"
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


def _upgrade_to_preflight_context() -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, PREFLIGHT_CONTEXT_REVISION)


def test_preflight_context_upgrade_creates_tables_and_contract_constraints() -> None:
    _upgrade_to_preflight_context()

    table_names = asyncio.run(_fetch_table_names())
    schema_objects = asyncio.run(_fetch_schema_object_names())

    assert table_names == PREFLIGHT_CONTEXT_TABLES
    assert "uq_ai_job_intake_context_job" in schema_objects
    assert "uq_ai_job_intake_context_chat_message" in schema_objects
    assert "fk_ai_job_intake_context_bundle_manifest" in schema_objects
    assert "fk_ai_job_execution_context_bundle_manifest" in schema_objects
    assert "chk_ai_job_execution_context_one_domain" in schema_objects
    assert "chk_ai_job_execution_context_intake_chat_only" in schema_objects
    assert "uq_medication_identification_id_medication" in schema_objects
    assert "fk_ai_job_execution_identification_matched_medication" in schema_objects
    assert "uq_ai_job_execution_identification_medication" in schema_objects
    assert "idx_ai_job_execution_identification_context" in schema_objects


def test_preflight_context_downgrade_blocks_when_context_data_exists() -> None:
    cfg = create_alembic_config()
    _upgrade_to_preflight_context()
    ids = asyncio.run(_seed_preflight_context_graph())

    try:
        with pytest.raises(RuntimeError, match="existing data"):
            command.downgrade(cfg, PREFLIGHT_CONTEXT_BASE_REVISION)
        assert asyncio.run(_fetch_table_names()) == PREFLIGHT_CONTEXT_TABLES
    finally:
        asyncio.run(_cleanup_preflight_fixture_graph(ids))
        asyncio.run(_cleanup_context_tables())
        command.downgrade(cfg, PREFLIGHT_CONTEXT_BASE_REVISION)
        command.upgrade(cfg, PREFLIGHT_CONTEXT_REVISION)


def test_preflight_context_downgrade_empty_schema_removes_tables() -> None:
    cfg = create_alembic_config()
    _upgrade_to_preflight_context()

    command.downgrade(cfg, PREFLIGHT_CONTEXT_BASE_REVISION)

    assert asyncio.run(_fetch_table_names()) == set()
    command.upgrade(cfg, PREFLIGHT_CONTEXT_REVISION)


def test_preflight_tables_join_existing_integrity_head() -> None:
    from scripts.ci.verify_database_head import read_database_head_state, validation_errors

    cfg = create_alembic_config()
    command.upgrade(cfg, "398293a4b5c6")
    command.upgrade(cfg, "head")
    assert asyncio.run(_fetch_table_names()) == PREFLIGHT_CONTEXT_TABLES

    async def verify() -> None:
        async with _connection() as connection:
            assert validation_errors("3983a4b5c6d7", await read_database_head_state(connection)) == []

    asyncio.run(verify())
