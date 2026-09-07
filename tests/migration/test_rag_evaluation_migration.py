"""RAG Evaluation Alembic schema tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAG_EVALUATION_BASE_REVISION = "164f3a2b1c0d"

RAG_EVALUATION_TABLES = {
    "eval_dataset",
    "eval_case",
    "eval_experiment",
    "eval_variant",
    "eval_run",
    "eval_case_result",
    "eval_metric",
    "eval_failure",
}


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


def create_alembic_database_url() -> str:
    return config.database_url


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
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
            {"table_names": list(RAG_EVALUATION_TABLES)},
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
            {"table_names": list(RAG_EVALUATION_TABLES)},
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
            {"table_names": list(RAG_EVALUATION_TABLES)},
        )
        return {
            *(row[0] for row in constraints),
            *(row[0] for row in indexes),
        }


async def _fetch_evaluation_check_definitions() -> list[str]:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT pg_get_constraintdef(pg_constraint.oid)
                FROM pg_constraint
                JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
                JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                WHERE pg_namespace.nspname = 'public'
                  AND pg_class.relname = ANY(:table_names)
                  AND pg_constraint.contype = 'c'
                """
            ),
            {"table_names": list(RAG_EVALUATION_TABLES)},
        )
        return [row[0] for row in result]


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


async def _seed_eval_dataset() -> str:
    dataset_id = str(uuid4())
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
                        :dataset_id, :dataset_key, '1.0.0', 'Synthetic Evaluation Dataset', 'DRAFT',
                        'rag-eval.schema-set', '1.2.0', :schema_set_sha256,
                        :manifest_hash, 'SYNTHETIC', 0
                    )
                    """
                ),
                {
                    "dataset_id": dataset_id,
                    "dataset_key": f"synthetic-{uuid4().hex[:10]}",
                    "schema_set_sha256": "a" * 64,
                    "manifest_hash": "b" * 64,
                },
            )
    return dataset_id


async def _cleanup_eval_dataset(dataset_id: str) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text("DELETE FROM eval_dataset WHERE id = :dataset_id"), {"dataset_id": dataset_id}
            )


async def _seed_eval_graph() -> dict[str, str]:
    ids = {
        "dataset_id": str(uuid4()),
        "case_id": str(uuid4()),
        "experiment_id": str(uuid4()),
        "variant_id": str(uuid4()),
        "run_id": str(uuid4()),
        "case_result_id": str(uuid4()),
    }
    unique_suffix = uuid4().hex[:10]

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
                        :dataset_id, :dataset_key, '1.0.0', 'Synthetic Evaluation Dataset', 'DRAFT',
                        'rag-eval.schema-set', '1.2.0', :schema_set_sha256,
                        :manifest_hash, 'SYNTHETIC', 1
                    )
                    """
                ),
                {
                    "dataset_id": ids["dataset_id"],
                    "dataset_key": f"synthetic-{unique_suffix}",
                    "schema_set_sha256": "a" * 64,
                    "manifest_hash": f"{unique_suffix:0<64}"[:64],
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_case (
                        id, dataset_id, case_key, case_version, partition,
                        experiment_type, input_hash, expected_scope_codes
                    )
                    VALUES (
                        :case_id, :dataset_id, 'case-001', '1.0.0', 'DEV',
                        'END_TO_END_RAG', :input_hash, '["ROUTINE_MEDICATION_GUIDE"]'::json
                    )
                    """
                ),
                {**ids, "input_hash": "c" * 64},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_experiment (
                        id, dataset_id, experiment_key, experiment_version,
                        experiment_type, policy_ref, policy_hash
                    )
                    VALUES (
                        :experiment_id, :dataset_id, :experiment_key, '1.0.0',
                        'END_TO_END_RAG', 'docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md', :policy_hash
                    )
                    """
                ),
                {**ids, "experiment_key": f"experiment-{unique_suffix}", "policy_hash": "d" * 64},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_variant (
                        id, experiment_id, variant_key, variant_role, config_hash
                    )
                    VALUES (:variant_id, :experiment_id, 'candidate', 'CANDIDATE', :config_hash)
                    """
                ),
                {**ids, "config_hash": "e" * 64},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_run (
                        id, run_key, dataset_id, experiment_id, variant_id, execution_status,
                        decision_status, git_commit_sha, dataset_manifest_hash
                    )
                    VALUES (
                        :run_id, :run_key, :dataset_id, :experiment_id, :variant_id, 'COMPLETED',
                        'PASS', 'abcdef1', :dataset_manifest_hash
                    )
                    """
                ),
                {**ids, "run_key": f"run-{unique_suffix}", "dataset_manifest_hash": f"{unique_suffix:1<64}"[:64]},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_case_result (
                        id, run_id, case_id, dataset_id, execution_status, decision_status,
                        result_summary_hash
                    )
                    VALUES (
                        :case_result_id, :run_id, :case_id, :dataset_id, 'COMPLETED', 'PASS', :result_summary_hash
                    )
                    """
                ),
                {**ids, "result_summary_hash": "f" * 64},
            )

    return ids


async def _cleanup_eval_graph(ids: dict[str, str]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM eval_failure WHERE run_id = :run_id"), ids)
            await connection.execute(text("DELETE FROM eval_failure WHERE case_result_id = :case_result_id"), ids)
            await connection.execute(text("DELETE FROM eval_metric WHERE run_id = :run_id"), ids)
            await connection.execute(text("DELETE FROM eval_metric WHERE case_result_id = :case_result_id"), ids)
            await connection.execute(text("DELETE FROM eval_case_result WHERE id = :case_result_id"), ids)
            await connection.execute(text("DELETE FROM eval_run WHERE id = :run_id"), ids)
            await connection.execute(text("DELETE FROM eval_variant WHERE id = :variant_id"), ids)
            await connection.execute(text("DELETE FROM eval_experiment WHERE id = :experiment_id"), ids)
            await connection.execute(text("DELETE FROM eval_case WHERE id = :case_id"), ids)
            await connection.execute(text("DELETE FROM eval_dataset WHERE id = :dataset_id"), ids)


async def _assert_integrity_error(statement: str, params: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(text(statement), params)


def test_rag_evaluation_tables_and_constraints_exist_after_alembic_upgrade() -> None:
    alembic_config = create_alembic_config()

    command.upgrade(alembic_config, "head")

    assert asyncio.run(_fetch_table_names()) == RAG_EVALUATION_TABLES

    schema_objects = asyncio.run(_fetch_schema_object_names())

    assert "uq_eval_dataset_key_version" in schema_objects
    assert "uq_eval_case_dataset_key" in schema_objects
    assert "uq_eval_experiment_key_version" in schema_objects
    assert "uq_eval_variant_experiment_key" in schema_objects
    assert "uq_eval_run_key" in schema_objects
    assert "uq_eval_case_result_run_case" in schema_objects
    assert "uq_eval_metric_run_metric" in schema_objects
    assert "uq_eval_metric_case_metric" in schema_objects
    assert "uq_eval_failure_run_code" in schema_objects
    assert "uq_eval_failure_case_code" in schema_objects

    assert "chk_eval_case_experiment_type" in schema_objects
    assert "chk_eval_run_incomplete_decision_null" in schema_objects
    assert "chk_eval_case_result_incomplete_decision_null" in schema_objects
    assert "chk_eval_metric_single_owner" in schema_objects
    assert "chk_eval_failure_single_owner" in schema_objects
    assert "chk_eval_metric_score_range" in schema_objects
    assert "chk_eval_metric_confidence_range" in schema_objects


def test_rag_evaluation_upgrade_uses_end_to_end_rag_and_rejects_end_to_end_final() -> None:
    alembic_config = create_alembic_config()

    command.upgrade(alembic_config, "head")

    check_definitions = "\n".join(asyncio.run(_fetch_evaluation_check_definitions()))

    assert "END_TO_END_RAG" in check_definitions
    assert "END_TO_END_FINAL" not in check_definitions


def test_rag_evaluation_empty_downgrade_roundtrips() -> None:
    alembic_config = create_alembic_config()

    try:
        command.upgrade(alembic_config, "head")

        command.downgrade(alembic_config, RAG_EVALUATION_BASE_REVISION)
        assert asyncio.run(_table_exists("eval_dataset")) is False

        command.upgrade(alembic_config, "head")
        assert asyncio.run(_table_exists("eval_dataset")) is True
    finally:
        command.upgrade(alembic_config, "head")


def test_rag_evaluation_downgrade_blocks_non_empty_tables_and_preserves_data() -> None:
    alembic_config = create_alembic_config()
    dataset_id: str | None = None

    try:
        command.upgrade(alembic_config, "head")
        dataset_id = asyncio.run(_seed_eval_dataset())

        with pytest.raises(RuntimeError, match="Cannot downgrade revision 164a9c8e7d6f"):
            command.downgrade(alembic_config, RAG_EVALUATION_BASE_REVISION)

        assert asyncio.run(_table_exists("eval_dataset")) is True
    finally:
        command.upgrade(alembic_config, "head")
        if dataset_id is not None:
            asyncio.run(_cleanup_eval_dataset(dataset_id))


def test_rag_evaluation_rejects_invalid_contract_values() -> None:
    alembic_config = create_alembic_config()
    dataset_id: str | None = None

    try:
        command.upgrade(alembic_config, "head")
        dataset_id = asyncio.run(_seed_eval_dataset())

        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_case (
                    id, dataset_id, case_key, case_version, partition,
                    experiment_type, input_hash, expected_scope_codes
                )
                VALUES (
                    :case_id, :dataset_id, 'case-invalid', '1.0.0', 'DEV',
                    'END_TO_END_FINAL', :input_hash, '["ROUTINE_MEDICATION_GUIDE"]'::json
                )
                """,
                {"case_id": str(uuid4()), "dataset_id": dataset_id, "input_hash": "c" * 64},
            )
        )
    finally:
        if dataset_id is not None:
            asyncio.run(_cleanup_eval_dataset(dataset_id))


def test_rag_evaluation_rejects_incomplete_decision_status() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_eval_graph())

        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_run (
                    id, run_key, dataset_id, experiment_id, variant_id, execution_status,
                    decision_status, git_commit_sha, dataset_manifest_hash
                )
                VALUES (
                    :id, :run_key, :dataset_id, :experiment_id, :variant_id, 'ERROR',
                    'FAIL', 'abcdef1', :dataset_manifest_hash
                )
                """,
                {
                    **ids,
                    "id": str(uuid4()),
                    "run_key": f"run-invalid-{uuid4().hex[:10]}",
                    "dataset_manifest_hash": "1" * 64,
                },
            )
        )
    finally:
        if ids is not None:
            asyncio.run(_cleanup_eval_graph(ids))


def test_rag_evaluation_rejects_metric_owner_and_range_violations() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_eval_graph())

        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_metric (
                    id, run_id, case_result_id, metric_scope, metric_key,
                    metric_version, metric_area, score
                )
                VALUES (
                    :id, :run_id, :case_result_id, 'RUN', 'precision',
                    '1.0.0', 'retrieval', 0.8
                )
                """,
                {**ids, "id": str(uuid4())},
            )
        )
        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_metric (
                    id, run_id, metric_scope, metric_key, metric_version,
                    metric_area, numerator, denominator
                )
                VALUES (
                    :id, :run_id, 'RUN', 'recall', '1.0.0',
                    'retrieval', 3, 2
                )
                """,
                {**ids, "id": str(uuid4())},
            )
        )
        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_metric (
                    id, run_id, metric_scope, metric_key, metric_version,
                    metric_area, score
                )
                VALUES (
                    :id, :run_id, 'RUN', 'grounding', '1.0.0',
                    'answer', 1.2
                )
                """,
                {**ids, "id": str(uuid4())},
            )
        )
        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_metric (
                    id, run_id, metric_scope, metric_key, metric_version,
                    metric_area, is_release_blocking, is_diagnostic
                )
                VALUES (
                    :id, :run_id, 'RUN', 'debug-only', '1.0.0',
                    'diagnostic', true, true
                )
                """,
                {**ids, "id": str(uuid4())},
            )
        )
    finally:
        if ids is not None:
            asyncio.run(_cleanup_eval_graph(ids))


def test_rag_evaluation_rejects_duplicate_metric_and_failure_scope_entries() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_eval_graph())

        async def insert_valid_metric_and_failure() -> None:
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO eval_metric (
                                id, run_id, metric_scope, metric_key,
                                metric_version, metric_area, score
                            )
                            VALUES (
                                :metric_id, :run_id, 'RUN', 'precision',
                                '1.0.0', 'retrieval', 0.9
                            )
                            """
                        ),
                        {**ids, "metric_id": str(uuid4())},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO eval_failure (
                                id, run_id, failure_scope, failure_code, failure_area
                            )
                            VALUES (
                                :failure_id, :run_id, 'RUN', 'TIMEOUT', 'worker'
                            )
                            """
                        ),
                        {**ids, "failure_id": str(uuid4())},
                    )

        asyncio.run(insert_valid_metric_and_failure())

        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_metric (
                    id, run_id, metric_scope, metric_key, metric_version,
                    metric_area, score
                )
                VALUES (
                    :id, :run_id, 'RUN', 'precision', '1.0.0',
                    'retrieval', 0.8
                )
                """,
                {**ids, "id": str(uuid4())},
            )
        )
        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_failure (
                    id, run_id, failure_scope, failure_code, failure_area
                )
                VALUES (:id, :run_id, 'RUN', 'TIMEOUT', 'worker')
                """,
                {**ids, "id": str(uuid4())},
            )
        )
    finally:
        if ids is not None:
            asyncio.run(_cleanup_eval_graph(ids))


async def _seed_eval_dataset_and_experiment() -> dict[str, str]:
    ids = {
        "dataset_id": str(uuid4()),
        "experiment_id": str(uuid4()),
        "variant_id": str(uuid4()),
    }
    unique_suffix = uuid4().hex[:10]

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
                        :dataset_id, :dataset_key, '1.0.0', 'Synthetic Evaluation Dataset', 'DRAFT',
                        'rag-eval.schema-set', '1.2.0', :schema_set_sha256,
                        :manifest_hash, 'SYNTHETIC', 0
                    )
                    """
                ),
                {
                    **ids,
                    "dataset_key": f"synthetic-extra-{unique_suffix}",
                    "schema_set_sha256": "a" * 64,
                    "manifest_hash": f"{unique_suffix:2<64}"[:64],
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
                        :experiment_id, :dataset_id, :experiment_key, '1.0.0',
                        'END_TO_END_RAG', 'docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md', :policy_hash
                    )
                    """
                ),
                {**ids, "experiment_key": f"experiment-extra-{unique_suffix}", "policy_hash": "d" * 64},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO eval_variant (
                        id, experiment_id, variant_key, variant_role, config_hash
                    )
                    VALUES (:variant_id, :experiment_id, 'candidate', 'CANDIDATE', :config_hash)
                    """
                ),
                {**ids, "config_hash": f"{unique_suffix:3<64}"[:64]},
            )
    return ids


async def _cleanup_eval_dataset_and_experiment(ids: dict[str, str]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM eval_variant WHERE id = :variant_id"), ids)
            await connection.execute(text("DELETE FROM eval_experiment WHERE id = :experiment_id"), ids)
            await connection.execute(text("DELETE FROM eval_case WHERE dataset_id = :dataset_id"), ids)
            await connection.execute(text("DELETE FROM eval_dataset WHERE id = :dataset_id"), ids)


def test_rag_evaluation_rejects_end_to_end_case_without_scope_codes() -> None:
    alembic_config = create_alembic_config()
    dataset_id: str | None = None

    try:
        command.upgrade(alembic_config, "head")
        dataset_id = asyncio.run(_seed_eval_dataset())

        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_case (
                    id, dataset_id, case_key, case_version, partition,
                    experiment_type, input_hash, expected_scope_codes
                )
                VALUES (
                    :case_id, :dataset_id, 'case-no-scope', '1.0.0', 'DEV',
                    'END_TO_END_RAG', :input_hash, '[]'::json
                )
                """,
                {"case_id": str(uuid4()), "dataset_id": dataset_id, "input_hash": "c" * 64},
            )
        )
    finally:
        if dataset_id is not None:
            asyncio.run(_cleanup_eval_dataset(dataset_id))


def test_rag_evaluation_rejects_run_variant_from_another_experiment() -> None:
    alembic_config = create_alembic_config()
    first_ids: dict[str, str] | None = None
    second_ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        first_ids = asyncio.run(_seed_eval_dataset_and_experiment())
        second_ids = asyncio.run(_seed_eval_dataset_and_experiment())

        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_run (
                    id, run_key, dataset_id, experiment_id, variant_id, execution_status,
                    git_commit_sha, dataset_manifest_hash
                )
                VALUES (
                    :id, :run_key, :dataset_id, :experiment_id, :variant_id, 'COMPLETED',
                    'abcdef1', :dataset_manifest_hash
                )
                """,
                {
                    "id": str(uuid4()),
                    "run_key": f"run-cross-variant-{uuid4().hex[:10]}",
                    "dataset_id": first_ids["dataset_id"],
                    "experiment_id": first_ids["experiment_id"],
                    "variant_id": second_ids["variant_id"],
                    "dataset_manifest_hash": "4" * 64,
                },
            )
        )
    finally:
        if second_ids is not None:
            asyncio.run(_cleanup_eval_dataset_and_experiment(second_ids))
        if first_ids is not None:
            asyncio.run(_cleanup_eval_dataset_and_experiment(first_ids))


def test_rag_evaluation_rejects_case_result_case_from_another_dataset() -> None:
    alembic_config = create_alembic_config()
    run_ids: dict[str, str] | None = None
    other_ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        run_ids = asyncio.run(_seed_eval_graph())
        other_ids = asyncio.run(_seed_eval_dataset_and_experiment())

        async def seed_other_case() -> str:
            case_id = str(uuid4())
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO eval_case (
                                id, dataset_id, case_key, case_version, partition,
                                experiment_type, input_hash, expected_scope_codes
                            )
                            VALUES (
                                :case_id, :dataset_id, 'case-other', '1.0.0', 'DEV',
                                'END_TO_END_RAG', :input_hash, '["ROUTINE_MEDICATION_GUIDE"]'::json
                            )
                            """
                        ),
                        {"case_id": case_id, "dataset_id": other_ids["dataset_id"], "input_hash": "c" * 64},
                    )
            return case_id

        other_case_id = asyncio.run(seed_other_case())
        asyncio.run(
            _assert_integrity_error(
                """
                INSERT INTO eval_case_result (
                    id, run_id, case_id, dataset_id, execution_status
                )
                VALUES (
                    :id, :run_id, :case_id, :dataset_id, 'COMPLETED'
                )
                """,
                {
                    "id": str(uuid4()),
                    "run_id": run_ids["run_id"],
                    "case_id": other_case_id,
                    "dataset_id": run_ids["dataset_id"],
                },
            )
        )
    finally:
        if other_ids is not None:
            asyncio.run(_cleanup_eval_dataset_and_experiment(other_ids))
        if run_ids is not None:
            asyncio.run(_cleanup_eval_graph(run_ids))
