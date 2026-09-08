from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op

revision = "164b6c7d8e9f"
down_revision = "169c3d4e5f6a"
branch_labels = None
depends_on = None


BUNDLE_STATUSES = ("BUILDING", "READY", "RETIRED", "FAILED")
ENVIRONMENT_STATUSES = ("ACTIVE", "SUSPENDED")
TRANSITION_KINDS = ("PLANNED_ACTIVATION", "EMERGENCY_ROLLBACK", "RESUME", "SUSPEND")
SOURCE_PURPOSES = ("CATALOG", "KNOWLEDGE", "CANDIDATE_INDEX_INPUT", "RULE", "GUIDELINE", "SAFETY_POLICY")
APPROVAL_STATUSES = ("PENDING", "APPROVED", "REJECTED")
EVALUATION_DECISIONS = ("PASS", "FAIL", "INCONCLUSIVE", "N/A")


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _has_rows(table_name: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first())


def _raise_if_runtime_data_exists() -> None:
    tables = (
        "rag_release_evaluation_approval",
        "rag_runtime_environment_transition",
        "rag_runtime_environment",
        "rag_runtime_bundle_source",
        "rag_runtime_release_bundle",
        "rag_runtime_execution_manifest",
    )
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE"))
    non_empty = [table for table in tables if _has_rows(table)]
    if non_empty:
        raise RuntimeError(
            "Refusing to downgrade RAG runtime bundle tables with existing data: " + ", ".join(non_empty)
        )


def upgrade() -> None:
    op.create_unique_constraint("uq_eval_run_id_decision_status", "eval_run", ["id", "decision_status"])

    op.create_table(
        "rag_runtime_execution_manifest",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("manifest_key", sa.String(length=120), nullable=False),
        sa.Column("manifest_version", sa.String(length=80), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("git_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("worker_artifact_ref", sa.String(length=255), nullable=True),
        sa.Column("model_ref", sa.String(length=255), nullable=True),
        sa.Column("prompt_ref", sa.String(length=255), nullable=True),
        sa.Column("parser_ref", sa.String(length=255), nullable=True),
        sa.Column("resolver_ref", sa.String(length=255), nullable=True),
        sa.Column("guard_policy_ref", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manifest_key", "manifest_version", name="uq_rag_runtime_manifest_key_version"),
        sa.UniqueConstraint("manifest_hash", name="uq_rag_runtime_manifest_hash"),
        sa.CheckConstraint("length(trim(manifest_key)) > 0", name="chk_rag_runtime_manifest_key_nonblank"),
        sa.CheckConstraint("length(trim(manifest_version)) > 0", name="chk_rag_runtime_manifest_version_nonblank"),
        sa.CheckConstraint("length(manifest_hash) = 64", name="chk_rag_runtime_manifest_hash_length"),
        sa.CheckConstraint("length(trim(schema_version)) > 0", name="chk_rag_runtime_manifest_schema_version_nonblank"),
        sa.CheckConstraint("length(git_commit_sha) BETWEEN 7 AND 40", name="chk_rag_runtime_manifest_git_sha_length"),
    )

    op.create_table(
        "rag_runtime_release_bundle",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("bundle_key", sa.String(length=120), nullable=False),
        sa.Column("bundle_version", sa.String(length=80), nullable=False),
        sa.Column("bundle_status", sa.String(length=20), nullable=False),
        sa.Column("execution_manifest_id", sa.CHAR(36), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_index_ref", sa.String(length=255), nullable=True),
        sa.Column("candidate_index_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("knowledge_index_ref", sa.String(length=255), nullable=True),
        sa.Column("knowledge_index_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("rule_set_ref", sa.String(length=255), nullable=True),
        sa.Column("guideline_set_ref", sa.String(length=255), nullable=True),
        sa.Column("safety_policy_ref", sa.String(length=255), nullable=True),
        sa.Column("governance_revision_ref", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["execution_manifest_id"],
            ["rag_runtime_execution_manifest.id"],
            name="fk_rag_runtime_bundle_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_key", "bundle_version", name="uq_rag_runtime_bundle_key_version"),
        sa.UniqueConstraint("bundle_manifest_hash", name="uq_rag_runtime_bundle_manifest_hash"),
        sa.UniqueConstraint("id", "bundle_manifest_hash", name="uq_rag_runtime_bundle_id_manifest_hash"),
        sa.CheckConstraint("length(trim(bundle_key)) > 0", name="chk_rag_runtime_bundle_key_nonblank"),
        sa.CheckConstraint("length(trim(bundle_version)) > 0", name="chk_rag_runtime_bundle_version_nonblank"),
        sa.CheckConstraint("length(bundle_manifest_hash) = 64", name="chk_rag_runtime_bundle_manifest_hash_length"),
        sa.CheckConstraint(f"bundle_status IN ({_sql_in_list(BUNDLE_STATUSES)})", name="chk_rag_runtime_bundle_status"),
        sa.CheckConstraint(
            "candidate_index_manifest_hash IS NULL OR length(candidate_index_manifest_hash) = 64",
            name="chk_rag_runtime_bundle_candidate_index_hash_length",
        ),
        sa.CheckConstraint(
            "knowledge_index_manifest_hash IS NULL OR length(knowledge_index_manifest_hash) = 64",
            name="chk_rag_runtime_bundle_knowledge_index_hash_length",
        ),
    )
    op.create_index("idx_rag_runtime_bundle_status", "rag_runtime_release_bundle", ["bundle_status"])

    op.create_table(
        "rag_runtime_bundle_source",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("bundle_id", sa.CHAR(36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("source_purpose", sa.String(length=30), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("selected_for_operation", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["bundle_id"],
            ["rag_runtime_release_bundle.id"],
            name="fk_rag_runtime_bundle_source_bundle",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_runtime_bundle_source_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bundle_id", "source_snapshot_id", "source_purpose", name="uq_rag_runtime_bundle_source_member"
        ),
        sa.CheckConstraint(
            f"source_purpose IN ({_sql_in_list(SOURCE_PURPOSES)})", name="chk_rag_runtime_bundle_source_purpose"
        ),
    )
    op.create_index("idx_rag_runtime_bundle_source_snapshot", "rag_runtime_bundle_source", ["source_snapshot_id"])

    op.create_table(
        "rag_runtime_environment",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("environment_code", sa.String(length=50), nullable=False),
        sa.Column("environment_status", sa.String(length=20), nullable=False),
        sa.Column("active_bundle_id", sa.CHAR(36), nullable=True),
        sa.Column("active_bundle_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("environment_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("governance_revision_ref", sa.String(length=255), nullable=True),
        sa.Column("safety_epoch", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_bundle_id", "active_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_runtime_environment_active_bundle_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("environment_code", name="uq_rag_runtime_environment_code"),
        sa.CheckConstraint("length(trim(environment_code)) > 0", name="chk_rag_runtime_environment_code_nonblank"),
        sa.CheckConstraint(
            f"environment_status IN ({_sql_in_list(ENVIRONMENT_STATUSES)})", name="chk_rag_runtime_environment_status"
        ),
        sa.CheckConstraint(
            "(active_bundle_id IS NULL AND active_bundle_manifest_hash IS NULL) OR (active_bundle_id IS NOT NULL AND active_bundle_manifest_hash IS NOT NULL)",
            name="chk_rag_runtime_environment_active_bundle_pair",
        ),
        sa.CheckConstraint(
            "active_bundle_manifest_hash IS NULL OR length(active_bundle_manifest_hash) = 64",
            name="chk_rag_runtime_environment_active_bundle_hash_length",
        ),
        sa.CheckConstraint("environment_revision >= 1", name="chk_rag_runtime_environment_revision_positive"),
        sa.CheckConstraint("safety_epoch >= 1", name="chk_rag_runtime_environment_safety_epoch_positive"),
    )
    op.create_index("idx_rag_runtime_environment_active_bundle", "rag_runtime_environment", ["active_bundle_id"])
    op.create_table(
        "rag_runtime_environment_transition",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("environment_id", sa.CHAR(36), nullable=False),
        sa.Column("transition_kind", sa.String(length=30), nullable=False),
        sa.Column("from_bundle_id", sa.CHAR(36), nullable=True),
        sa.Column("from_bundle_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("to_bundle_id", sa.CHAR(36), nullable=True),
        sa.Column("to_bundle_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("environment_revision", sa.Integer(), nullable=False),
        sa.Column("governance_revision_ref", sa.String(length=255), nullable=True),
        sa.Column("safety_epoch", sa.Integer(), nullable=False),
        sa.Column("guard_decision_ref", sa.String(length=255), nullable=False),
        sa.Column("transition_reason_code", sa.String(length=80), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["rag_runtime_environment.id"],
            name="fk_rag_runtime_transition_environment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_bundle_id", "from_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_runtime_transition_from_bundle_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_bundle_id", "to_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_runtime_transition_to_bundle_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"transition_kind IN ({_sql_in_list(TRANSITION_KINDS)})", name="chk_rag_runtime_transition_kind"
        ),
        sa.CheckConstraint(
            "(from_bundle_id IS NULL AND from_bundle_manifest_hash IS NULL) OR (from_bundle_id IS NOT NULL AND from_bundle_manifest_hash IS NOT NULL)",
            name="chk_rag_runtime_transition_from_bundle_pair",
        ),
        sa.CheckConstraint(
            "(to_bundle_id IS NULL AND to_bundle_manifest_hash IS NULL) OR (to_bundle_id IS NOT NULL AND to_bundle_manifest_hash IS NOT NULL)",
            name="chk_rag_runtime_transition_to_bundle_pair",
        ),
        sa.CheckConstraint(
            "from_bundle_manifest_hash IS NULL OR length(from_bundle_manifest_hash) = 64",
            name="chk_rag_runtime_transition_from_hash_length",
        ),
        sa.CheckConstraint(
            "to_bundle_manifest_hash IS NULL OR length(to_bundle_manifest_hash) = 64",
            name="chk_rag_runtime_transition_to_hash_length",
        ),
        sa.CheckConstraint("environment_revision >= 1", name="chk_rag_runtime_transition_revision_positive"),
        sa.CheckConstraint("safety_epoch >= 1", name="chk_rag_runtime_transition_safety_epoch_positive"),
        sa.CheckConstraint(
            "length(trim(guard_decision_ref)) > 0", name="chk_rag_runtime_transition_guard_ref_nonblank"
        ),
    )
    op.create_index("idx_rag_runtime_transition_environment", "rag_runtime_environment_transition", ["environment_id"])
    op.create_index("idx_rag_runtime_transition_to_bundle", "rag_runtime_environment_transition", ["to_bundle_id"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_rag_runtime_transition_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'rag_runtime_environment_transition is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_rag_runtime_transition_append_only
        BEFORE UPDATE OR DELETE ON rag_runtime_environment_transition
        FOR EACH ROW EXECUTE FUNCTION prevent_rag_runtime_transition_mutation();
        """
    )

    op.create_table(
        "rag_release_evaluation_approval",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("bundle_id", sa.CHAR(36), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("eval_run_id", sa.CHAR(36), nullable=False),
        sa.Column("eval_decision_status", sa.String(length=20), nullable=False),
        sa.Column("approval_scope", sa.String(length=80), nullable=False),
        sa.Column("approval_status", sa.String(length=20), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["bundle_id", "bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_release_eval_approval_bundle_manifest",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["eval_run_id", "eval_decision_status"],
            ["eval_run.id", "eval_run.decision_status"],
            name="fk_rag_release_eval_approval_run_decision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", "eval_run_id", "approval_scope", name="uq_rag_release_eval_approval_scope"),
        sa.CheckConstraint(
            "length(bundle_manifest_hash) = 64", name="chk_rag_release_eval_approval_bundle_hash_length"
        ),
        sa.CheckConstraint("length(trim(approval_scope)) > 0", name="chk_rag_release_eval_approval_scope_nonblank"),
        sa.CheckConstraint(
            f"approval_status IN ({_sql_in_list(APPROVAL_STATUSES)})", name="chk_rag_release_eval_approval_status"
        ),
        sa.CheckConstraint(
            "eval_decision_status IN (" + _sql_in_list(EVALUATION_DECISIONS) + ")",
            name="chk_rag_release_eval_approval_decision_status",
        ),
        sa.CheckConstraint(
            "approval_status <> 'APPROVED' OR (eval_decision_status = 'PASS' AND approved_by IS NOT NULL AND length(trim(approved_by)) > 0 AND approved_at IS NOT NULL)",
            name="chk_rag_release_eval_approval_requires_pass",
        ),
    )
    op.create_index("idx_rag_release_eval_approval_eval_run", "rag_release_evaluation_approval", ["eval_run_id"])


def downgrade() -> None:
    _raise_if_runtime_data_exists()
    op.drop_index("idx_rag_release_eval_approval_eval_run", table_name="rag_release_evaluation_approval")
    op.drop_table("rag_release_evaluation_approval")
    op.execute("DROP TRIGGER IF EXISTS trg_rag_runtime_transition_append_only ON rag_runtime_environment_transition")
    op.execute("DROP FUNCTION IF EXISTS prevent_rag_runtime_transition_mutation()")
    op.drop_index("idx_rag_runtime_transition_to_bundle", table_name="rag_runtime_environment_transition")
    op.drop_index("idx_rag_runtime_transition_environment", table_name="rag_runtime_environment_transition")
    op.drop_table("rag_runtime_environment_transition")
    op.drop_index("idx_rag_runtime_environment_active_bundle", table_name="rag_runtime_environment")
    op.drop_table("rag_runtime_environment")
    op.drop_index("idx_rag_runtime_bundle_source_snapshot", table_name="rag_runtime_bundle_source")
    op.drop_table("rag_runtime_bundle_source")
    op.drop_index("idx_rag_runtime_bundle_status", table_name="rag_runtime_release_bundle")
    op.drop_table("rag_runtime_release_bundle")
    op.drop_table("rag_runtime_execution_manifest")
    op.drop_constraint("uq_eval_run_id_decision_status", "eval_run", type_="unique")
