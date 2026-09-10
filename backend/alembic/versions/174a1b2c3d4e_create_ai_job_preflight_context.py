"""create AI Job preflight context tables

Revision ID: 174a1b2c3d4e
Revises: 201a1b2c3d4e
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "174a1b2c3d4e"
down_revision: str | Sequence[str] | None = "201a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_rows(table_name: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first())


def _raise_if_context_data_exists() -> None:
    tables = (
        "ai_job_execution_identification",
        "ai_job_execution_context",
        "ai_job_intake_context",
    )
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE"))
    non_empty = [table for table in tables if _has_rows(table)]
    if non_empty:
        raise RuntimeError(
            "Refusing to downgrade AI Job preflight context tables with existing data: " + ", ".join(non_empty)
        )


def upgrade() -> None:
    op.create_table(
        "ai_job_intake_context",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("ai_job_id", sa.CHAR(length=36), nullable=False),
        sa.Column("chat_message_id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_version_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_environment_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_environment_revision", sa.Integer(), nullable=False),
        sa.Column("runtime_release_bundle_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_release_bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_execution_manifest_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_execution_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_guard_decision_ref", sa.String(length=255), nullable=False),
        sa.Column("question_digest", sa.String(length=64), nullable=True),
        sa.Column("patient_context_digest", sa.String(length=64), nullable=True),
        sa.Column("context_schema_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("runtime_environment_revision >= 1", name="chk_ai_job_intake_context_revision_positive"),
        sa.CheckConstraint(
            "length(runtime_release_bundle_manifest_hash) = 64",
            name="chk_ai_job_intake_context_bundle_hash_length",
        ),
        sa.CheckConstraint(
            "length(runtime_execution_manifest_hash) = 64",
            name="chk_ai_job_intake_context_manifest_hash_length",
        ),
        sa.CheckConstraint(
            "question_digest IS NULL OR length(question_digest) = 64",
            name="chk_ai_job_intake_context_question_digest_length",
        ),
        sa.CheckConstraint(
            "patient_context_digest IS NULL OR length(patient_context_digest) = 64",
            name="chk_ai_job_intake_context_patient_digest_length",
        ),
        sa.CheckConstraint(
            "length(trim(runtime_guard_decision_ref)) > 0",
            name="chk_ai_job_intake_context_guard_ref_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(context_schema_version)) > 0",
            name="chk_ai_job_intake_context_schema_nonblank",
        ),
        sa.ForeignKeyConstraint(["ai_job_id"], ["ai_job.id"], name="fk_ai_job_intake_context_job", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["chat_message_id"],
            ["chat_message.id"],
            name="fk_ai_job_intake_context_chat_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prescription_version_id"],
            ["prescription_version.id"],
            name="fk_ai_job_intake_context_prescription_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_environment_id"],
            ["rag_runtime_environment.id"],
            name="fk_ai_job_intake_context_environment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_release_bundle_id", "runtime_release_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_ai_job_intake_context_bundle_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_execution_manifest_id"],
            ["rag_runtime_execution_manifest.id"],
            name="fk_ai_job_intake_context_execution_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_job_id", name="uq_ai_job_intake_context_job"),
        sa.UniqueConstraint("chat_message_id", name="uq_ai_job_intake_context_chat_message"),
    )
    op.create_index("idx_ai_job_intake_context_job", "ai_job_intake_context", ["ai_job_id"])
    op.create_index(
        "idx_ai_job_intake_context_prescription_version",
        "ai_job_intake_context",
        ["prescription_version_id"],
    )

    op.create_table(
        "ai_job_execution_context",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("ai_job_id", sa.CHAR(length=36), nullable=False),
        sa.Column("intake_context_id", sa.CHAR(length=36), nullable=True),
        sa.Column("guide_id", sa.CHAR(length=36), nullable=True),
        sa.Column("chat_message_id", sa.CHAR(length=36), nullable=True),
        sa.Column("prescription_version_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_environment_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_environment_revision", sa.Integer(), nullable=False),
        sa.Column("runtime_release_bundle_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_release_bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_execution_manifest_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_execution_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_guard_decision_ref", sa.String(length=255), nullable=False),
        sa.Column("patient_context_digest", sa.String(length=64), nullable=True),
        sa.Column("source_scope_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("context_schema_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(guide_id IS NOT NULL AND chat_message_id IS NULL) OR (guide_id IS NULL AND chat_message_id IS NOT NULL)",
            name="chk_ai_job_execution_context_one_domain",
        ),
        sa.CheckConstraint(
            "intake_context_id IS NULL OR chat_message_id IS NOT NULL",
            name="chk_ai_job_execution_context_intake_chat_only",
        ),
        sa.CheckConstraint("runtime_environment_revision >= 1", name="chk_ai_job_execution_context_revision_positive"),
        sa.CheckConstraint(
            "length(runtime_release_bundle_manifest_hash) = 64",
            name="chk_ai_job_execution_context_bundle_hash_length",
        ),
        sa.CheckConstraint(
            "length(runtime_execution_manifest_hash) = 64",
            name="chk_ai_job_execution_context_manifest_hash_length",
        ),
        sa.CheckConstraint(
            "patient_context_digest IS NULL OR length(patient_context_digest) = 64",
            name="chk_ai_job_execution_context_patient_digest_length",
        ),
        sa.CheckConstraint(
            "source_scope_manifest_hash IS NULL OR length(source_scope_manifest_hash) = 64",
            name="chk_ai_job_execution_context_scope_hash_length",
        ),
        sa.CheckConstraint(
            "length(trim(runtime_guard_decision_ref)) > 0",
            name="chk_ai_job_execution_context_guard_ref_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(context_schema_version)) > 0",
            name="chk_ai_job_execution_context_schema_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["ai_job_id"], ["ai_job.id"], name="fk_ai_job_execution_context_job", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["intake_context_id"],
            ["ai_job_intake_context.id"],
            name="fk_ai_job_execution_context_intake",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["guide_id"], ["guide.id"], name="fk_ai_job_execution_context_guide", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["chat_message_id"],
            ["chat_message.id"],
            name="fk_ai_job_execution_context_chat_message",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prescription_version_id"],
            ["prescription_version.id"],
            name="fk_ai_job_execution_context_prescription_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_environment_id"],
            ["rag_runtime_environment.id"],
            name="fk_ai_job_execution_context_environment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_release_bundle_id", "runtime_release_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_ai_job_execution_context_bundle_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_execution_manifest_id"],
            ["rag_runtime_execution_manifest.id"],
            name="fk_ai_job_execution_context_execution_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_job_id", name="uq_ai_job_execution_context_job"),
    )
    op.create_index("idx_ai_job_execution_context_job", "ai_job_execution_context", ["ai_job_id"])
    op.create_index(
        "idx_ai_job_execution_context_prescription_version",
        "ai_job_execution_context",
        ["prescription_version_id"],
    )

    op.create_table(
        "ai_job_execution_identification",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("execution_context_id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_identification_id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_version_medication_id", sa.CHAR(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_context_id"],
            ["ai_job_execution_context.id"],
            name="fk_ai_job_execution_identification_context",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["medication_identification_id"],
            ["medication_identification.id"],
            name="fk_ai_job_execution_identification_identification",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prescription_version_medication_id"],
            ["prescription_version_medication.id"],
            name="fk_ai_job_execution_identification_medication",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_context_id",
            "medication_identification_id",
            name="uq_ai_job_execution_identification_identification",
        ),
        sa.UniqueConstraint(
            "execution_context_id",
            "prescription_version_medication_id",
            name="uq_ai_job_execution_identification_medication",
        ),
    )
    op.create_index(
        "idx_ai_job_execution_identification_context",
        "ai_job_execution_identification",
        ["execution_context_id"],
    )


def downgrade() -> None:
    _raise_if_context_data_exists()
    op.drop_index("idx_ai_job_execution_identification_context", table_name="ai_job_execution_identification")
    op.drop_table("ai_job_execution_identification")
    op.drop_index("idx_ai_job_execution_context_prescription_version", table_name="ai_job_execution_context")
    op.drop_index("idx_ai_job_execution_context_job", table_name="ai_job_execution_context")
    op.drop_table("ai_job_execution_context")
    op.drop_index("idx_ai_job_intake_context_prescription_version", table_name="ai_job_intake_context")
    op.drop_index("idx_ai_job_intake_context_job", table_name="ai_job_intake_context")
    op.drop_table("ai_job_intake_context")
