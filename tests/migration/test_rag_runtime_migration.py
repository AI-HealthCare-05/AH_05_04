from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAG_RUNTIME_REVISION = "164b6c7d8e9f"
RAG_RUNTIME_BASE_REVISION = "169b2c3d4e5f"
RAG_RUNTIME_TABLES = {
    "rag_runtime_execution_manifest",
    "rag_runtime_release_bundle",
    "rag_runtime_bundle_source",
    "rag_runtime_environment",
    "rag_runtime_environment_transition",
    "rag_release_evaluation_approval",
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
            {"table_names": list(RAG_RUNTIME_TABLES)},
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
            {"table_names": list(RAG_RUNTIME_TABLES | {"eval_run"})},
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
            {"table_names": list(RAG_RUNTIME_TABLES)},
        )
        triggers = await connection.execute(
            text(
                """
                SELECT trigger_name
                FROM information_schema.triggers
                WHERE event_object_schema = 'public'
                  AND event_object_table = 'rag_runtime_environment_transition'
                """
            )
        )
        return {*(row[0] for row in constraints), *(row[0] for row in indexes), *(row[0] for row in triggers)}


async def _table_exists(table_name: str) -> bool:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return bool(result.scalar_one())


async def _seed_manifest_and_bundle() -> dict[str, str]:
    ids = {
        "manifest_id": str(uuid4()),
        "bundle_id": str(uuid4()),
        "manifest_key": f"runtime-{uuid4().hex[:10]}",
        "bundle_key": f"bundle-{uuid4().hex[:10]}",
    }
    async with _connection() as connection:
        async with connection.begin():
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
                {
                    **ids,
                    "manifest_hash": "a" * 64,
                },
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
                {
                    **ids,
                    "bundle_hash": "b" * 64,
                },
            )
    ids["bundle_hash"] = "b" * 64
    return ids


async def _seed_passed_eval_run() -> dict[str, str]:
    ids = {
        "eval_dataset_id": str(uuid4()),
        "eval_experiment_id": str(uuid4()),
        "eval_variant_id": str(uuid4()),
        "eval_run_id": str(uuid4()),
    }
    suffix = uuid4().hex[:10]
    dataset_manifest_hash = f"{suffix:0<64}"[:64]
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_dataset (
                        id, dataset_key, dataset_version, display_name, dataset_status,
                        schema_set_id, schema_set_version, schema_set_sha256,
                        manifest_hash, source_classification, case_count
                    )
                    VALUES (
                        :eval_dataset_id, :dataset_key, '1.0.0', 'Runtime Evaluation Dataset', 'DRAFT',
                        'rag-eval.schema-set', '1.2.0', :schema_set_sha256,
                        :dataset_manifest_hash, 'SYNTHETIC', 0
                    )
                    """
                ),
                {
                    **ids,
                    "dataset_key": f"runtime-eval-{suffix}",
                    "schema_set_sha256": "a" * 64,
                    "dataset_manifest_hash": dataset_manifest_hash,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_experiment (
                        id, dataset_id, experiment_key, experiment_version,
                        experiment_type, policy_ref, policy_hash
                    )
                    VALUES (
                        :eval_experiment_id, :eval_dataset_id, :experiment_key, '1.0.0',
                        'END_TO_END_RAG', 'docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md', :policy_hash
                    )
                    """
                ),
                {**ids, "experiment_key": f"runtime-exp-{suffix}", "policy_hash": "b" * 64},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_variant (
                        id, experiment_id, variant_key, variant_role, config_hash
                    )
                    VALUES (:eval_variant_id, :eval_experiment_id, 'candidate', 'CANDIDATE', :config_hash)
                    """
                ),
                {**ids, "config_hash": "c" * 64},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_run (
                        id, run_key, dataset_id, experiment_id, variant_id, experiment_type, execution_status,
                        decision_status, git_commit_sha, dataset_manifest_hash
                    )
                    VALUES (
                        :eval_run_id, :run_key, :eval_dataset_id, :eval_experiment_id, :eval_variant_id,
                        'END_TO_END_RAG', 'COMPLETED', 'PASS', 'abcdef1', :dataset_manifest_hash
                    )
                    """
                ),
                {**ids, "run_key": f"runtime-run-{suffix}", "dataset_manifest_hash": dataset_manifest_hash},
            )
    ids["dataset_manifest_hash"] = dataset_manifest_hash
    return ids


async def _cleanup_eval_run_graph(ids: dict[str, str]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM eval_run WHERE id = :eval_run_id"), ids)
            await connection.execute(text("DELETE FROM eval_variant WHERE id = :eval_variant_id"), ids)
            await connection.execute(text("DELETE FROM eval_experiment WHERE id = :eval_experiment_id"), ids)
            await connection.execute(text("DELETE FROM eval_dataset WHERE id = :eval_dataset_id"), ids)


async def _write_runtime_manifest_and_wait(
    *,
    writer_ready: Event,
    release_writer: Event,
) -> str:
    manifest_id = str(uuid4())
    async with _connection() as connection:
        async with connection.begin():
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
                {
                    "manifest_id": manifest_id,
                    "manifest_key": f"runtime-lock-{uuid4().hex[:10]}",
                    "manifest_hash": "9" * 64,
                },
            )
            writer_ready.set()
            while not release_writer.wait(timeout=0.05):
                await asyncio.sleep(0)
    return manifest_id


async def _wait_for_runtime_downgrade_lock() -> None:
    async with _connection() as connection:
        for _ in range(100):
            result = await connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_locks AS lock_info
                        JOIN pg_class AS table_info
                          ON table_info.oid = lock_info.relation
                        JOIN pg_namespace AS namespace_info
                          ON namespace_info.oid = table_info.relnamespace
                        WHERE namespace_info.nspname = 'public'
                          AND table_info.relname IN (
                              'rag_release_evaluation_approval',
                              'rag_runtime_environment_transition',
                              'rag_runtime_environment',
                              'rag_runtime_bundle_source',
                              'rag_runtime_release_bundle',
                              'rag_runtime_execution_manifest'
                          )
                          AND lock_info.mode = 'AccessExclusiveLock'
                          AND lock_info.granted = false
                    )
                    """
                )
            )
            if result.scalar_one():
                return
            await asyncio.sleep(0.05)
    raise AssertionError("Runtime downgrade did not wait for an ACCESS EXCLUSIVE lock.")


async def _cleanup_runtime_tables() -> None:
    async with _connection() as connection:
        async with connection.begin():
            for table_name in (
                "rag_release_evaluation_approval",
                "rag_runtime_environment_transition",
                "rag_runtime_environment",
                "rag_runtime_bundle_source",
                "rag_runtime_release_bundle",
                "rag_runtime_execution_manifest",
            ):
                table_exists = await connection.execute(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": table_name},
                )
                if table_exists.scalar_one() is not None:
                    await connection.execute(text(f"DELETE FROM {table_name}"))


@pytest.fixture(autouse=True)
def _clean_runtime_data_after_test() -> Iterator[None]:
    yield
    asyncio.run(_cleanup_runtime_tables())


def _upgrade_to_runtime() -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "head")
    asyncio.run(_cleanup_runtime_tables())
    command.downgrade(cfg, RAG_RUNTIME_BASE_REVISION)
    command.upgrade(cfg, RAG_RUNTIME_REVISION)


def test_rag_runtime_upgrade_creates_tables_and_constraints() -> None:
    _upgrade_to_runtime()

    table_names = asyncio.run(_fetch_table_names())
    schema_objects = asyncio.run(_fetch_schema_object_names())

    assert table_names == RAG_RUNTIME_TABLES
    assert "uq_eval_run_id_decision_status" in schema_objects
    assert "fk_rag_runtime_bundle_source_snapshot" in schema_objects
    assert "fk_rag_runtime_environment_active_bundle_manifest" in schema_objects
    assert "fk_rag_release_eval_approval_bundle_manifest" in schema_objects
    assert "fk_rag_release_eval_approval_run_decision" in schema_objects
    assert "fk_rag_runtime_transition_to_bundle_manifest" in schema_objects
    assert "chk_rag_release_eval_approval_requires_pass" in schema_objects
    assert "trg_rag_runtime_transition_append_only" in schema_objects


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        (
            """
            INSERT INTO rag_runtime_release_bundle (
                id, bundle_key, bundle_version, bundle_status,
                execution_manifest_id, bundle_manifest_hash, candidate_index_manifest_hash
            )
            VALUES (:id, 'bundle-bad-index', '1.0.0', 'BUILDING', :manifest_id, :bundle_hash, 'short')
            """,
            {},
        ),
        (
            """
            INSERT INTO rag_runtime_environment (
                id, environment_code, environment_status,
                active_bundle_id, active_bundle_manifest_hash
            )
            VALUES (:id, 'local-bad-hash', 'SUSPENDED', :bundle_id, :wrong_hash)
            """,
            {"wrong_hash": "c" * 64},
        ),
        (
            """
            INSERT INTO rag_runtime_release_bundle (
                id, bundle_key, bundle_version, bundle_status,
                execution_manifest_id, bundle_manifest_hash
            )
            VALUES (:id, 'bundle-active-status', '1.0.0', 'ACTIVE', :manifest_id, :bundle_hash)
            """,
            {},
        ),
        (
            """
            INSERT INTO rag_runtime_bundle_source (
                id, bundle_id, source_snapshot_id, source_purpose
            )
            VALUES (:id, :bundle_id, :missing_snapshot_id, 'CATALOG')
            """,
            {"missing_snapshot_id": "11111111-1111-1111-1111-111111111111"},
        ),
    ],
)
def test_rag_runtime_constraints_reject_invalid_rows(sql: str, params: dict[str, str]) -> None:
    _upgrade_to_runtime()
    ids = asyncio.run(_seed_manifest_and_bundle())

    async def _insert_invalid() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(sql),
                    {
                        **ids,
                        **params,
                        "id": str(uuid4()),
                        "manifest_id": ids["manifest_id"],
                        "bundle_id": ids["bundle_id"],
                        "bundle_hash": "d" * 64,
                    },
                )

    with pytest.raises(DBAPIError):
        asyncio.run(_insert_invalid())


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        (
            """
            INSERT INTO rag_runtime_execution_manifest (
                id, manifest_key, manifest_version, manifest_hash,
                schema_version, git_commit_sha
            )
            VALUES (
                :id, :manifest_key, '1.0.0', :new_manifest_hash,
                'runtime-manifest-v1', 'abcdef1'
            )
            """,
            {"new_manifest_hash": "e" * 64},
        ),
        (
            """
            INSERT INTO rag_runtime_release_bundle (
                id, bundle_key, bundle_version, bundle_status,
                execution_manifest_id, bundle_manifest_hash
            )
            VALUES (
                :id, :bundle_key, '1.0.0', 'BUILDING',
                :manifest_id, :new_bundle_hash
            )
            """,
            {"new_bundle_hash": "f" * 64},
        ),
        (
            """
            INSERT INTO rag_release_evaluation_approval (
                id, bundle_id, bundle_manifest_hash, eval_run_id, eval_decision_status,
                approval_scope, approval_status
            )
            VALUES (
                :id, :bundle_id, :bundle_hash, :missing_eval_run_id, 'PASS',
                '   ', 'PENDING'
            )
            """,
            {"missing_eval_run_id": "22222222-2222-2222-2222-222222222222"},
        ),
    ],
)
def test_rag_runtime_unique_and_nonblank_constraints_reject_invalid_rows(
    sql: str,
    params: dict[str, str],
) -> None:
    _upgrade_to_runtime()
    ids = asyncio.run(_seed_manifest_and_bundle())

    async def _insert_invalid() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(sql),
                    {
                        **ids,
                        **params,
                        "id": str(uuid4()),
                    },
                )

    with pytest.raises(DBAPIError):
        asyncio.run(_insert_invalid())


@pytest.mark.parametrize(
    ("guard_decision_ref", "environment_revision", "safety_epoch"),
    [
        ("   ", 1, 1),
        ("guard-decision:test", 0, 1),
        ("guard-decision:test", 1, 0),
    ],
)
def test_rag_runtime_transition_checks_reject_invalid_rows(
    guard_decision_ref: str,
    environment_revision: int,
    safety_epoch: int,
) -> None:
    _upgrade_to_runtime()
    ids = asyncio.run(_seed_manifest_and_bundle())

    async def _insert_invalid_transition() -> None:
        async with _connection() as connection:
            async with connection.begin():
                result = await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_environment (
                            id, environment_code, environment_status
                        )
                        VALUES (:environment_id, :environment_code, 'SUSPENDED')
                        RETURNING id
                        """
                    ),
                    {
                        "environment_id": str(uuid4()),
                        "environment_code": f"local-{uuid4().hex[:8]}",
                    },
                )
                environment_id = result.scalar_one()
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_environment_transition (
                            id, environment_id, transition_kind,
                            to_bundle_id, to_bundle_manifest_hash,
                            environment_revision, safety_epoch,
                            guard_decision_ref, created_by
                        )
                        VALUES (
                            :transition_id, :environment_id, 'PLANNED_ACTIVATION',
                            :bundle_id, :bundle_hash,
                            :environment_revision, :safety_epoch,
                            :guard_decision_ref, 'backend-test'
                        )
                        """
                    ),
                    {
                        **ids,
                        "environment_id": environment_id,
                        "transition_id": str(uuid4()),
                        "guard_decision_ref": guard_decision_ref,
                        "environment_revision": environment_revision,
                        "safety_epoch": safety_epoch,
                    },
                )

    with pytest.raises(DBAPIError):
        asyncio.run(_insert_invalid_transition())


def test_rag_runtime_approved_eval_approval_requires_pass() -> None:
    _upgrade_to_runtime()
    ids = asyncio.run(_seed_manifest_and_bundle())

    async def _insert_invalid_approval() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_release_evaluation_approval (
                            id, bundle_id, bundle_manifest_hash, eval_run_id, eval_decision_status,
                            approval_scope, approval_status, approved_by, approved_at
                        )
                        VALUES (
                            :id, :bundle_id, :bundle_hash, :missing_eval_run_id, 'FAIL',
                            'END_TO_END_RAG', 'APPROVED', 'reviewer', now()
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "bundle_id": ids["bundle_id"],
                        "bundle_hash": ids["bundle_hash"],
                        "missing_eval_run_id": "22222222-2222-2222-2222-222222222222",
                    },
                )

    with pytest.raises(DBAPIError):
        asyncio.run(_insert_invalid_approval())


def test_rag_runtime_eval_approval_must_reference_existing_run_decision() -> None:
    _upgrade_to_runtime()
    ids = asyncio.run(_seed_manifest_and_bundle())

    async def _insert_missing_eval_run_approval() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_release_evaluation_approval (
                            id, bundle_id, bundle_manifest_hash, eval_run_id, eval_decision_status,
                            approval_scope, approval_status
                        )
                        VALUES (
                            :id, :bundle_id, :bundle_hash, :missing_eval_run_id, 'PASS',
                            'END_TO_END_RAG', 'PENDING'
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "bundle_id": ids["bundle_id"],
                        "bundle_hash": ids["bundle_hash"],
                        "missing_eval_run_id": "22222222-2222-2222-2222-222222222222",
                    },
                )

    with pytest.raises(DBAPIError):
        asyncio.run(_insert_missing_eval_run_approval())


def test_rag_runtime_eval_approval_bundle_hash_must_match_bundle() -> None:
    _upgrade_to_runtime()
    runtime_ids = asyncio.run(_seed_manifest_and_bundle())
    eval_ids = asyncio.run(_seed_passed_eval_run())

    async def _insert_mismatched_approval() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_release_evaluation_approval (
                            id, bundle_id, bundle_manifest_hash, eval_run_id, eval_decision_status,
                            approval_scope, approval_status, approved_by, approved_at
                        )
                        VALUES (
                            :id, :bundle_id, :wrong_bundle_hash, :eval_run_id, 'PASS',
                            'END_TO_END_RAG', 'APPROVED', 'reviewer', now()
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "bundle_id": runtime_ids["bundle_id"],
                        "wrong_bundle_hash": "c" * 64,
                        "eval_run_id": eval_ids["eval_run_id"],
                    },
                )

    try:
        with pytest.raises(DBAPIError):
            asyncio.run(_insert_mismatched_approval())
    finally:
        asyncio.run(_cleanup_eval_run_graph(eval_ids))


def test_rag_runtime_downgrade_empty_schema_removes_tables() -> None:
    cfg = create_alembic_config()
    _upgrade_to_runtime()

    command.downgrade(cfg, RAG_RUNTIME_BASE_REVISION)

    assert not asyncio.run(_table_exists("rag_runtime_release_bundle"))
    command.upgrade(cfg, "head")


def test_rag_runtime_downgrade_blocks_when_data_exists() -> None:
    cfg = create_alembic_config()
    _upgrade_to_runtime()
    asyncio.run(_seed_manifest_and_bundle())

    try:
        with pytest.raises(RuntimeError, match="existing data"):
            command.downgrade(cfg, RAG_RUNTIME_BASE_REVISION)
    finally:
        asyncio.run(_cleanup_runtime_tables())
        command.downgrade(cfg, RAG_RUNTIME_BASE_REVISION)
        command.upgrade(cfg, "head")


def test_rag_runtime_downgrade_blocks_concurrent_runtime_writes() -> None:
    cfg = create_alembic_config()
    writer_ready = Event()
    release_writer = Event()
    writer_future: Future[str] | None = None
    downgrade_future: Future[None] | None = None

    _upgrade_to_runtime()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            writer_future = executor.submit(
                asyncio.run,
                _write_runtime_manifest_and_wait(
                    writer_ready=writer_ready,
                    release_writer=release_writer,
                ),
            )
            if not writer_ready.wait(timeout=10):
                if writer_future.done():
                    writer_future.result()
                raise AssertionError("Concurrent Runtime writer did not reach the uncommitted state.")

            downgrade_future = executor.submit(command.downgrade, cfg, RAG_RUNTIME_BASE_REVISION)
            asyncio.run(_wait_for_runtime_downgrade_lock())

            release_writer.set()
            writer_future.result(timeout=10)
            with pytest.raises(RuntimeError, match="existing data"):
                downgrade_future.result(timeout=10)
    finally:
        release_writer.set()
        if writer_future is not None and not writer_future.done():
            writer_future.cancel()
        if downgrade_future is not None and not downgrade_future.done():
            downgrade_future.cancel()
        asyncio.run(_cleanup_runtime_tables())
        command.downgrade(cfg, RAG_RUNTIME_BASE_REVISION)
        command.upgrade(cfg, "head")


def test_rag_runtime_transition_history_is_append_only() -> None:
    _upgrade_to_runtime()
    ids = asyncio.run(_seed_manifest_and_bundle())

    async def _insert_and_mutate_transition() -> None:
        async with _connection() as connection:
            async with connection.begin():
                result = await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_environment (
                            id, environment_code, environment_status,
                            active_bundle_id, active_bundle_manifest_hash
                        )
                        VALUES (
                            :environment_id, :environment_code, 'SUSPENDED',
                            :bundle_id, :bundle_hash
                        )
                        RETURNING id
                        """
                    ),
                    {
                        **ids,
                        "environment_id": str(uuid4()),
                        "environment_code": f"local-{uuid4().hex[:8]}",
                    },
                )
                environment_id = result.scalar_one()
                result = await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_environment_transition (
                            id, environment_id, transition_kind,
                            to_bundle_id, to_bundle_manifest_hash,
                            environment_revision, safety_epoch,
                            guard_decision_ref, created_by
                        )
                        VALUES (
                            :transition_id, :environment_id, 'PLANNED_ACTIVATION',
                            :bundle_id, :bundle_hash,
                            1, 1, 'guard-decision:test', 'backend-test'
                        )
                        RETURNING id
                        """
                    ),
                    {
                        **ids,
                        "environment_id": environment_id,
                        "transition_id": str(uuid4()),
                    },
                )
                transition_id = result.scalar_one()
                await connection.execute(
                    text(
                        """
                        UPDATE rag_runtime_environment_transition
                        SET transition_reason_code = 'changed'
                        WHERE id = :transition_id
                        """
                    ),
                    {"transition_id": transition_id},
                )

    with pytest.raises(DBAPIError):
        asyncio.run(_insert_and_mutate_transition())
