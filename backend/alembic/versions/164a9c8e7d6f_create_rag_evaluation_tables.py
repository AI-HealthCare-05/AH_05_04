"""create rag evaluation tables

Revision ID: 164a9c8e7d6f
Revises: 164f3a2b1c0d
Create Date: 2026-09-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "164a9c8e7d6f"
down_revision: str | Sequence[str] | None = "164f3a2b1c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPERIMENT_TYPE_VALUES = (
    "KNOWLEDGE_RETRIEVAL",
    "ANSWER_GROUNDING_SAFETY",
    "END_TO_END_RAG",
)
_DATASET_PARTITION_VALUES = (
    "AUTHORING",
    "DEV",
    "HOLDOUT",
    "SAFETY_REGRESSION",
)
_DATASET_STATUS_VALUES = (
    "DRAFT",
    "REVIEWED",
    "APPROVED",
    "FROZEN",
    "RETIRED",
)
_VARIANT_ROLE_VALUES = (
    "BASELINE",
    "CANDIDATE",
    "DIAGNOSTIC",
)
_EXECUTION_STATUS_VALUES = (
    "NOT_IMPLEMENTED",
    "NOT_EVALUATED",
    "INVALID",
    "ERROR",
    "COMPLETED",
)
_DECISION_STATUS_VALUES = (
    "PASS",
    "FAIL",
    "INCONCLUSIVE",
    "N/A",
)
_METRIC_SCOPE_VALUES = (
    "RUN",
    "CASE",
)
_FAILURE_SCOPE_VALUES = (
    "RUN",
    "CASE",
)

_RAG_EVALUATION_TABLES = (
    "eval_failure",
    "eval_metric",
    "eval_case_result",
    "eval_run",
    "eval_variant",
    "eval_experiment",
    "eval_case",
    "eval_dataset",
)


def _sql_in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _ensure_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    for table_name in _RAG_EVALUATION_TABLES:
        connection.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))

    non_empty_tables: list[str] = []
    for table_name in _RAG_EVALUATION_TABLES:
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table_name}")).scalar_one()
        if count:
            non_empty_tables.append(table_name)

    if non_empty_tables:
        joined_tables = ", ".join(non_empty_tables)
        raise RuntimeError(
            "Cannot downgrade revision 164a9c8e7d6f while RAG Evaluation data exists. "
            f"Non-empty tables: {joined_tables}. Use a forward-fix migration or an approved backup and "
            "data-retention rollback procedure instead."
        )


def upgrade() -> None:
    op.create_table(
        "eval_dataset",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("dataset_key", sa.String(length=120), nullable=False),
        sa.Column("dataset_version", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("dataset_status", sa.String(length=20), nullable=False),
        sa.Column("schema_set_id", sa.String(length=120), nullable=False),
        sa.Column("schema_set_version", sa.String(length=80), nullable=False),
        sa.Column("schema_set_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_uri", sa.String(length=500), nullable=True),
        sa.Column("source_classification", sa.String(length=80), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(dataset_key)) > 0", name="chk_eval_dataset_key_nonblank"),
        sa.CheckConstraint("length(trim(dataset_version)) > 0", name="chk_eval_dataset_version_nonblank"),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="chk_eval_dataset_display_name_nonblank"),
        sa.CheckConstraint("length(schema_set_sha256) = 64", name="chk_eval_dataset_schema_sha256_length"),
        sa.CheckConstraint("length(manifest_hash) = 64", name="chk_eval_dataset_manifest_hash_length"),
        sa.CheckConstraint("case_count >= 0", name="chk_eval_dataset_case_count"),
        sa.CheckConstraint(
            f"dataset_status IN ({_sql_in_list(_DATASET_STATUS_VALUES)})",
            name="chk_eval_dataset_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_key", "dataset_version", name="uq_eval_dataset_key_version"),
        sa.UniqueConstraint("manifest_hash", name="uq_eval_dataset_manifest_hash"),
    )

    op.create_table(
        "eval_case",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("dataset_id", sa.CHAR(length=36), nullable=False),
        sa.Column("case_key", sa.String(length=120), nullable=False),
        sa.Column("case_version", sa.String(length=80), nullable=False),
        sa.Column("partition", sa.String(length=30), nullable=False),
        sa.Column("experiment_type", sa.String(length=40), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("gold_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "question_template",
            sa.String(length=255),
            nullable=True,
            comment="Non-sensitive template identifier or synthetic prompt template label only; patient-derived free text is prohibited.",
        ),
        sa.Column(
            "source_segment",
            sa.String(length=255),
            nullable=True,
            comment="Non-sensitive synthetic segment identifier only; source excerpts, OCR text, and prescription text are prohibited.",
        ),
        sa.Column("medication_family", sa.String(length=120), nullable=True),
        sa.Column("transform_origin", sa.String(length=120), nullable=True),
        sa.Column("expected_scope_codes", sa.JSON(), nullable=True),
        sa.Column("expected_outcome_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(case_key)) > 0", name="chk_eval_case_key_nonblank"),
        sa.CheckConstraint("length(trim(case_version)) > 0", name="chk_eval_case_version_nonblank"),
        sa.CheckConstraint("length(input_hash) = 64", name="chk_eval_case_input_hash_length"),
        sa.CheckConstraint("gold_hash IS NULL OR length(gold_hash) = 64", name="chk_eval_case_gold_hash_length"),
        sa.CheckConstraint(
            f"partition IN ({_sql_in_list(_DATASET_PARTITION_VALUES)})",
            name="chk_eval_case_partition",
        ),
        sa.CheckConstraint(
            f"experiment_type IN ({_sql_in_list(_EXPERIMENT_TYPE_VALUES)})",
            name="chk_eval_case_experiment_type",
        ),
        sa.CheckConstraint(
            "experiment_type != 'END_TO_END_RAG' OR "
            "(expected_scope_codes IS NOT NULL AND json_typeof(expected_scope_codes) = 'array' "
            "AND json_array_length(expected_scope_codes) > 0)",
            name="chk_eval_case_end_to_end_scope_codes_nonempty",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_dataset.id"], name="fk_eval_case_dataset"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "case_key", name="uq_eval_case_dataset_key"),
        sa.UniqueConstraint("id", "dataset_id", name="uq_eval_case_id_dataset"),
    )
    op.create_index("idx_eval_case_dataset_partition", "eval_case", ["dataset_id", "partition"])

    op.create_table(
        "eval_experiment",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("dataset_id", sa.CHAR(length=36), nullable=False),
        sa.Column("experiment_key", sa.String(length=120), nullable=False),
        sa.Column("experiment_version", sa.String(length=80), nullable=False),
        sa.Column("experiment_type", sa.String(length=40), nullable=False),
        sa.Column("policy_ref", sa.String(length=255), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_set_ref", sa.String(length=255), nullable=True),
        sa.Column("rubric_ref", sa.String(length=255), nullable=True),
        sa.Column("is_release_gate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(experiment_key)) > 0", name="chk_eval_experiment_key_nonblank"),
        sa.CheckConstraint("length(trim(experiment_version)) > 0", name="chk_eval_experiment_version_nonblank"),
        sa.CheckConstraint("length(trim(policy_ref)) > 0", name="chk_eval_experiment_policy_ref_nonblank"),
        sa.CheckConstraint("length(policy_hash) = 64", name="chk_eval_experiment_policy_hash_length"),
        sa.CheckConstraint(
            f"experiment_type IN ({_sql_in_list(_EXPERIMENT_TYPE_VALUES)})",
            name="chk_eval_experiment_type",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_dataset.id"], name="fk_eval_experiment_dataset"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_key", "experiment_version", name="uq_eval_experiment_key_version"),
        sa.UniqueConstraint("id", "dataset_id", name="uq_eval_experiment_id_dataset"),
    )
    op.create_index("idx_eval_experiment_dataset", "eval_experiment", ["dataset_id"])

    op.create_table(
        "eval_variant",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("experiment_id", sa.CHAR(length=36), nullable=False),
        sa.Column("variant_key", sa.String(length=120), nullable=False),
        sa.Column("variant_role", sa.String(length=20), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_bundle_ref", sa.String(length=255), nullable=True),
        sa.Column("source_snapshot_ref", sa.String(length=255), nullable=True),
        sa.Column("candidate_index_ref", sa.String(length=255), nullable=True),
        sa.Column("knowledge_index_ref", sa.String(length=255), nullable=True),
        sa.Column("model_ref", sa.String(length=255), nullable=True),
        sa.Column("prompt_ref", sa.String(length=255), nullable=True),
        sa.Column("parser_ref", sa.String(length=255), nullable=True),
        sa.Column("embedding_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(variant_key)) > 0", name="chk_eval_variant_key_nonblank"),
        sa.CheckConstraint("length(config_hash) = 64", name="chk_eval_variant_config_hash_length"),
        sa.CheckConstraint(
            f"variant_role IN ({_sql_in_list(_VARIANT_ROLE_VALUES)})",
            name="chk_eval_variant_role",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["eval_experiment.id"], name="fk_eval_variant_experiment"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "variant_key", name="uq_eval_variant_experiment_key"),
        sa.UniqueConstraint("experiment_id", "config_hash", name="uq_eval_variant_experiment_config"),
        sa.UniqueConstraint("id", "experiment_id", name="uq_eval_variant_id_experiment"),
    )
    op.create_index("idx_eval_variant_experiment", "eval_variant", ["experiment_id"])

    op.create_table(
        "eval_run",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("run_key", sa.String(length=120), nullable=False),
        sa.Column("dataset_id", sa.CHAR(length=36), nullable=False),
        sa.Column("experiment_id", sa.CHAR(length=36), nullable=False),
        sa.Column("variant_id", sa.CHAR(length=36), nullable=False),
        sa.Column("execution_status", sa.String(length=30), nullable=False),
        sa.Column("decision_status", sa.String(length=20), nullable=True),
        sa.Column("git_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("dataset_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("execution_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("candidate_guard_ref", sa.String(length=255), nullable=True),
        sa.Column("blocking_execution_statuses", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(run_key)) > 0", name="chk_eval_run_key_nonblank"),
        sa.CheckConstraint("length(git_commit_sha) BETWEEN 7 AND 40", name="chk_eval_run_git_commit_sha_length"),
        sa.CheckConstraint("length(dataset_manifest_hash) = 64", name="chk_eval_run_dataset_hash_length"),
        sa.CheckConstraint(
            "execution_manifest_hash IS NULL OR length(execution_manifest_hash) = 64",
            name="chk_eval_run_execution_manifest_hash_length",
        ),
        sa.CheckConstraint(
            f"execution_status IN ({_sql_in_list(_EXECUTION_STATUS_VALUES)})",
            name="chk_eval_run_execution_status",
        ),
        sa.CheckConstraint(
            f"decision_status IS NULL OR decision_status IN ({_sql_in_list(_DECISION_STATUS_VALUES)})",
            name="chk_eval_run_decision_status",
        ),
        sa.CheckConstraint(
            "execution_status = 'COMPLETED' OR decision_status IS NULL",
            name="chk_eval_run_incomplete_decision_null",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at",
            name="chk_eval_run_completed_after_started",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_dataset.id"], name="fk_eval_run_dataset"),
        sa.ForeignKeyConstraint(
            ["experiment_id", "dataset_id"],
            ["eval_experiment.id", "eval_experiment.dataset_id"],
            name="fk_eval_run_experiment_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id", "experiment_id"],
            ["eval_variant.id", "eval_variant.experiment_id"],
            name="fk_eval_run_variant_experiment",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_key", name="uq_eval_run_key"),
        sa.UniqueConstraint("id", "dataset_id", name="uq_eval_run_id_dataset"),
    )
    op.create_index("idx_eval_run_dataset", "eval_run", ["dataset_id"])
    op.create_index("idx_eval_run_experiment", "eval_run", ["experiment_id"])
    op.create_index("idx_eval_run_variant", "eval_run", ["variant_id"])

    op.create_table(
        "eval_case_result",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("run_id", sa.CHAR(length=36), nullable=False),
        sa.Column("case_id", sa.CHAR(length=36), nullable=False),
        sa.Column("dataset_id", sa.CHAR(length=36), nullable=False),
        sa.Column("execution_status", sa.String(length=30), nullable=False),
        sa.Column("decision_status", sa.String(length=20), nullable=True),
        sa.Column("request_guard_ref", sa.String(length=255), nullable=True),
        sa.Column("result_summary_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "non_sensitive_summary",
            sa.JSON(),
            nullable=True,
            comment="Structured non-sensitive counters, enum codes, and metric labels only; free text, model output, retrieved chunks, patient data, OCR text, and prescription text are prohibited.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            f"execution_status IN ({_sql_in_list(_EXECUTION_STATUS_VALUES)})",
            name="chk_eval_case_result_execution_status",
        ),
        sa.CheckConstraint(
            f"decision_status IS NULL OR decision_status IN ({_sql_in_list(_DECISION_STATUS_VALUES)})",
            name="chk_eval_case_result_decision_status",
        ),
        sa.CheckConstraint(
            "execution_status = 'COMPLETED' OR decision_status IS NULL",
            name="chk_eval_case_result_incomplete_decision_null",
        ),
        sa.CheckConstraint(
            "result_summary_hash IS NULL OR length(result_summary_hash) = 64",
            name="chk_eval_case_result_summary_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "dataset_id"],
            ["eval_case.id", "eval_case.dataset_id"],
            name="fk_eval_case_result_case_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "dataset_id"],
            ["eval_run.id", "eval_run.dataset_id"],
            name="fk_eval_case_result_run_dataset",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "case_id", name="uq_eval_case_result_run_case"),
    )
    op.create_index("idx_eval_case_result_case", "eval_case_result", ["case_id"])
    op.create_index("idx_eval_case_result_dataset", "eval_case_result", ["dataset_id"])
    op.create_index("idx_eval_case_result_run", "eval_case_result", ["run_id"])

    op.create_table(
        "eval_metric",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("run_id", sa.CHAR(length=36), nullable=True),
        sa.Column("case_result_id", sa.CHAR(length=36), nullable=True),
        sa.Column("metric_scope", sa.String(length=10), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("metric_version", sa.String(length=80), nullable=False),
        sa.Column("metric_area", sa.String(length=80), nullable=False),
        sa.Column("numerator", sa.Integer(), nullable=True),
        sa.Column("denominator", sa.Integer(), nullable=True),
        sa.Column("score", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("confidence_lower", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("confidence_upper", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("is_release_blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_diagnostic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(metric_key)) > 0", name="chk_eval_metric_key_nonblank"),
        sa.CheckConstraint("length(trim(metric_version)) > 0", name="chk_eval_metric_version_nonblank"),
        sa.CheckConstraint("length(trim(metric_area)) > 0", name="chk_eval_metric_area_nonblank"),
        sa.CheckConstraint(
            f"metric_scope IN ({_sql_in_list(_METRIC_SCOPE_VALUES)})",
            name="chk_eval_metric_scope",
        ),
        sa.CheckConstraint(
            "(metric_scope = 'RUN' AND run_id IS NOT NULL AND case_result_id IS NULL) OR "
            "(metric_scope = 'CASE' AND run_id IS NULL AND case_result_id IS NOT NULL)",
            name="chk_eval_metric_single_owner",
        ),
        sa.CheckConstraint("numerator IS NULL OR numerator >= 0", name="chk_eval_metric_numerator_nonnegative"),
        sa.CheckConstraint("denominator IS NULL OR denominator >= 0", name="chk_eval_metric_denominator_nonnegative"),
        sa.CheckConstraint(
            "numerator IS NULL OR denominator IS NULL OR numerator <= denominator",
            name="chk_eval_metric_numerator_lte_denominator",
        ),
        sa.CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="chk_eval_metric_score_range"),
        sa.CheckConstraint(
            "(confidence_lower IS NULL AND confidence_upper IS NULL) OR "
            "(confidence_lower IS NOT NULL AND confidence_upper IS NOT NULL AND "
            "confidence_lower >= 0 AND confidence_upper <= 1 AND confidence_lower <= confidence_upper)",
            name="chk_eval_metric_confidence_range",
        ),
        sa.CheckConstraint(
            "NOT (is_release_blocking AND is_diagnostic)",
            name="chk_eval_metric_diagnostic_not_release_blocking",
        ),
        sa.ForeignKeyConstraint(["case_result_id"], ["eval_case_result.id"], name="fk_eval_metric_case_result"),
        sa.ForeignKeyConstraint(["run_id"], ["eval_run.id"], name="fk_eval_metric_run"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_eval_metric_run_metric",
        "eval_metric",
        ["run_id", "metric_key", "metric_version"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL AND case_result_id IS NULL"),
    )
    op.create_index(
        "uq_eval_metric_case_metric",
        "eval_metric",
        ["case_result_id", "metric_key", "metric_version"],
        unique=True,
        postgresql_where=sa.text("case_result_id IS NOT NULL AND run_id IS NULL"),
    )

    op.create_table(
        "eval_failure",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("run_id", sa.CHAR(length=36), nullable=True),
        sa.Column("case_result_id", sa.CHAR(length=36), nullable=True),
        sa.Column("failure_scope", sa.String(length=10), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=False),
        sa.Column("failure_area", sa.String(length=80), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "non_sensitive_context",
            sa.JSON(),
            nullable=True,
            comment="Structured non-sensitive failure codes, counters, and artifact references only; free text, model output, retrieved chunks, patient data, OCR text, and prescription text are prohibited.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(failure_code)) > 0", name="chk_eval_failure_code_nonblank"),
        sa.CheckConstraint("length(trim(failure_area)) > 0", name="chk_eval_failure_area_nonblank"),
        sa.CheckConstraint(
            f"failure_scope IN ({_sql_in_list(_FAILURE_SCOPE_VALUES)})",
            name="chk_eval_failure_scope",
        ),
        sa.CheckConstraint(
            "(failure_scope = 'RUN' AND run_id IS NOT NULL AND case_result_id IS NULL) OR "
            "(failure_scope = 'CASE' AND run_id IS NULL AND case_result_id IS NOT NULL)",
            name="chk_eval_failure_single_owner",
        ),
        sa.CheckConstraint(
            "context_hash IS NULL OR length(context_hash) = 64",
            name="chk_eval_failure_context_hash_length",
        ),
        sa.ForeignKeyConstraint(["case_result_id"], ["eval_case_result.id"], name="fk_eval_failure_case_result"),
        sa.ForeignKeyConstraint(["run_id"], ["eval_run.id"], name="fk_eval_failure_run"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_eval_failure_run_code",
        "eval_failure",
        ["run_id", "failure_code", "failure_area"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL AND case_result_id IS NULL"),
    )
    op.create_index(
        "uq_eval_failure_case_code",
        "eval_failure",
        ["case_result_id", "failure_code", "failure_area"],
        unique=True,
        postgresql_where=sa.text("case_result_id IS NOT NULL AND run_id IS NULL"),
    )


def downgrade() -> None:
    _ensure_downgrade_is_data_safe(op.get_bind())

    op.drop_index("uq_eval_failure_case_code", table_name="eval_failure")
    op.drop_index("uq_eval_failure_run_code", table_name="eval_failure")
    op.drop_table("eval_failure")
    op.drop_index("uq_eval_metric_case_metric", table_name="eval_metric")
    op.drop_index("uq_eval_metric_run_metric", table_name="eval_metric")
    op.drop_table("eval_metric")
    op.drop_index("idx_eval_case_result_run", table_name="eval_case_result")
    op.drop_index("idx_eval_case_result_dataset", table_name="eval_case_result", if_exists=True)
    op.drop_index("idx_eval_case_result_case", table_name="eval_case_result")
    op.drop_table("eval_case_result")
    op.drop_index("idx_eval_run_variant", table_name="eval_run")
    op.drop_index("idx_eval_run_experiment", table_name="eval_run")
    op.drop_index("idx_eval_run_dataset", table_name="eval_run", if_exists=True)
    op.drop_table("eval_run")
    op.drop_index("idx_eval_variant_experiment", table_name="eval_variant")
    op.drop_table("eval_variant")
    op.drop_index("idx_eval_experiment_dataset", table_name="eval_experiment")
    op.drop_table("eval_experiment")
    op.drop_index("idx_eval_case_dataset_partition", table_name="eval_case")
    op.drop_table("eval_case")
    op.drop_table("eval_dataset")
