"""harden AI Job preflight context internal bindings

Revision ID: 428a1b2c3d4e
Revises: 203a1b2c3d4e
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "428a1b2c3d4e"
down_revision: str | Sequence[str] | None = "203a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTEXT_TABLES = (
    "ai_job_execution_identification",
    "ai_job_execution_context",
    "ai_job_intake_context",
)


def _has_rows(table_name: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first())


def _raise_if_context_data_exists() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE " + ", ".join(_CONTEXT_TABLES) + " IN ACCESS EXCLUSIVE MODE"))
    non_empty = [table for table in _CONTEXT_TABLES if _has_rows(table)]
    if non_empty:
        raise RuntimeError(
            "Refusing to downgrade AI Job preflight context hardening with existing data: " + ", ".join(non_empty)
        )


def _raise_if_nullable_question_digest_exists() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE ai_job_intake_context IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT 1 FROM ai_job_intake_context WHERE question_digest IS NULL LIMIT 1")).first():
        raise RuntimeError(
            "Refusing to require ai_job_intake_context.question_digest while existing rows have NULL digest"
        )


def upgrade() -> None:
    _raise_if_nullable_question_digest_exists()

    op.create_unique_constraint(
        "uq_rag_runtime_manifest_id_hash",
        "rag_runtime_execution_manifest",
        ["id", "manifest_hash"],
    )
    op.create_unique_constraint(
        "uq_prescription_version_medication_id_version",
        "prescription_version_medication",
        ["id", "prescription_version_id"],
    )
    op.create_unique_constraint(
        "uq_rag_runtime_bundle_id_hash_execution_manifest",
        "rag_runtime_release_bundle",
        ["id", "bundle_manifest_hash", "execution_manifest_id"],
    )
    op.create_unique_constraint(
        "uq_ai_job_intake_context_job_id",
        "ai_job_intake_context",
        ["ai_job_id", "id"],
    )
    op.create_unique_constraint(
        "uq_ai_job_execution_context_id_version",
        "ai_job_execution_context",
        ["id", "prescription_version_id"],
    )

    op.drop_constraint(
        "chk_ai_job_intake_context_question_digest_length",
        "ai_job_intake_context",
        type_="check",
    )
    op.alter_column(
        "ai_job_intake_context",
        "question_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_check_constraint(
        "chk_ai_job_intake_context_question_digest_length",
        "ai_job_intake_context",
        "length(question_digest) = 64",
    )

    op.create_foreign_key(
        "fk_ai_job_intake_context_bundle_execution_manifest",
        "ai_job_intake_context",
        "rag_runtime_release_bundle",
        ["runtime_release_bundle_id", "runtime_release_bundle_manifest_hash", "runtime_execution_manifest_id"],
        ["id", "bundle_manifest_hash", "execution_manifest_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_job_intake_context_execution_manifest_hash",
        "ai_job_intake_context",
        "rag_runtime_execution_manifest",
        ["runtime_execution_manifest_id", "runtime_execution_manifest_hash"],
        ["id", "manifest_hash"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_job_execution_context_bundle_execution_manifest",
        "ai_job_execution_context",
        "rag_runtime_release_bundle",
        ["runtime_release_bundle_id", "runtime_release_bundle_manifest_hash", "runtime_execution_manifest_id"],
        ["id", "bundle_manifest_hash", "execution_manifest_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_job_execution_context_same_job_intake",
        "ai_job_execution_context",
        "ai_job_intake_context",
        ["ai_job_id", "intake_context_id"],
        ["ai_job_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ai_job_execution_context_execution_manifest_hash",
        "ai_job_execution_context",
        "rag_runtime_execution_manifest",
        ["runtime_execution_manifest_id", "runtime_execution_manifest_hash"],
        ["id", "manifest_hash"],
        ondelete="RESTRICT",
    )

    op.add_column("ai_job_execution_identification", sa.Column("prescription_version_id", sa.CHAR(length=36)))
    op.execute(
        sa.text(
            """
            UPDATE ai_job_execution_identification AS member
            SET prescription_version_id = ctx.prescription_version_id
            FROM ai_job_execution_context AS ctx
            WHERE ctx.id = member.execution_context_id
            """
        )
    )
    op.alter_column(
        "ai_job_execution_identification",
        "prescription_version_id",
        existing_type=sa.CHAR(length=36),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_ai_job_execution_identification_context_version",
        "ai_job_execution_identification",
        "ai_job_execution_context",
        ["execution_context_id", "prescription_version_id"],
        ["id", "prescription_version_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_ai_job_execution_identification_medication_version",
        "ai_job_execution_identification",
        "prescription_version_medication",
        ["prescription_version_medication_id", "prescription_version_id"],
        ["id", "prescription_version_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    _raise_if_context_data_exists()

    op.drop_constraint(
        "fk_ai_job_execution_identification_medication_version",
        "ai_job_execution_identification",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_job_execution_identification_context_version",
        "ai_job_execution_identification",
        type_="foreignkey",
    )
    op.drop_column("ai_job_execution_identification", "prescription_version_id")

    op.drop_constraint(
        "fk_ai_job_execution_context_execution_manifest_hash",
        "ai_job_execution_context",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_job_execution_context_bundle_execution_manifest",
        "ai_job_execution_context",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_job_execution_context_same_job_intake",
        "ai_job_execution_context",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_job_intake_context_execution_manifest_hash",
        "ai_job_intake_context",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ai_job_intake_context_bundle_execution_manifest",
        "ai_job_intake_context",
        type_="foreignkey",
    )

    op.drop_constraint(
        "chk_ai_job_intake_context_question_digest_length",
        "ai_job_intake_context",
        type_="check",
    )
    op.alter_column(
        "ai_job_intake_context",
        "question_digest",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.create_check_constraint(
        "chk_ai_job_intake_context_question_digest_length",
        "ai_job_intake_context",
        "question_digest IS NULL OR length(question_digest) = 64",
    )

    op.drop_constraint("uq_ai_job_execution_context_id_version", "ai_job_execution_context", type_="unique")
    op.drop_constraint("uq_ai_job_intake_context_job_id", "ai_job_intake_context", type_="unique")
    op.drop_constraint(
        "uq_rag_runtime_bundle_id_hash_execution_manifest",
        "rag_runtime_release_bundle",
        type_="unique",
    )
    op.drop_constraint(
        "uq_prescription_version_medication_id_version",
        "prescription_version_medication",
        type_="unique",
    )
    op.drop_constraint("uq_rag_runtime_manifest_id_hash", "rag_runtime_execution_manifest", type_="unique")
