"""Persist exact Guide retrieval binding authority (#180 B1).

Revision ID: 921a1b2c3d4e
Revises: 2ba3431f4ef2
"""

import sqlalchemy as sa
from alembic import op

revision = "921a1b2c3d4e"
down_revision = "2ba3431f4ef2"
branch_labels = None
depends_on = None

TABLE = "guide_retrieval_binding_manifest"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("manifest_version", sa.String(80), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("runtime_release_bundle_id", sa.CHAR(36), nullable=False),
        sa.Column("runtime_release_bundle_manifest_hash", sa.String(64), nullable=False),
        sa.Column("runtime_execution_manifest_id", sa.CHAR(36), nullable=False),
        sa.Column("runtime_execution_manifest_hash", sa.String(64), nullable=False),
        sa.Column("knowledge_index_id", sa.CHAR(36), nullable=False),
        sa.Column("evidence_index_code", sa.String(120), nullable=False),
        sa.Column("evidence_index_version", sa.String(80), nullable=False),
        sa.Column("evidence_index_configuration_hash", sa.String(64), nullable=False),
        sa.Column("member_bindings_json", sa.JSON(), nullable=False),
        sa.Column("retrieval_configuration_json", sa.JSON(), nullable=False),
        sa.Column("retrieval_configuration_hash", sa.String(64), nullable=False),
        sa.Column("filter_snapshot_code", sa.String(120), nullable=False),
        sa.Column("filter_snapshot_version", sa.String(80), nullable=False),
        sa.Column("filter_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_guide_retrieval_binding_manifest"),
        sa.UniqueConstraint("manifest_hash", name="uq_guide_retrieval_binding_manifest_hash"),
        sa.UniqueConstraint("id", "manifest_hash", name="uq_guide_retrieval_binding_manifest_id_hash"),
        sa.ForeignKeyConstraint(
            ["runtime_release_bundle_id", "runtime_release_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_guide_retrieval_binding_bundle_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_execution_manifest_id", "runtime_execution_manifest_hash"],
            ["rag_runtime_execution_manifest.id", "rag_runtime_execution_manifest.manifest_hash"],
            name="fk_guide_retrieval_binding_execution_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["knowledge_index_id"], ["rag_knowledge_index.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("length(trim(manifest_version)) > 0", name="chk_guide_retrieval_binding_version_nonblank"),
        sa.CheckConstraint("manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_guide_retrieval_binding_manifest_hash"),
        sa.CheckConstraint(
            "runtime_release_bundle_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_guide_retrieval_binding_bundle_hash",
        ),
        sa.CheckConstraint(
            "runtime_execution_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_guide_retrieval_binding_execution_hash",
        ),
        sa.CheckConstraint(
            "evidence_index_configuration_hash ~ '^[0-9a-f]{64}$'",
            name="chk_guide_retrieval_binding_index_hash",
        ),
        sa.CheckConstraint(
            "retrieval_configuration_hash ~ '^[0-9a-f]{64}$'",
            name="chk_guide_retrieval_binding_config_hash",
        ),
        sa.CheckConstraint(
            "filter_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="chk_guide_retrieval_binding_filter_hash",
        ),
        sa.CheckConstraint(
            "source_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_guide_retrieval_binding_source_hash",
        ),
    )
    op.add_column("ai_job_execution_context", sa.Column("guide_retrieval_binding_manifest_id", sa.CHAR(36)))
    op.add_column(
        "ai_job_execution_context",
        sa.Column("guide_retrieval_binding_manifest_hash", sa.String(64)),
    )
    op.create_check_constraint(
        "chk_ai_job_execution_context_guide_retrieval_binding_pair",
        "ai_job_execution_context",
        "(guide_retrieval_binding_manifest_id IS NULL) = (guide_retrieval_binding_manifest_hash IS NULL)",
    )
    op.create_check_constraint(
        "chk_ai_job_execution_context_guide_retrieval_binding_domain",
        "ai_job_execution_context",
        "guide_retrieval_binding_manifest_id IS NULL OR guide_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_ai_job_execution_context_guide_retrieval_binding",
        "ai_job_execution_context",
        TABLE,
        ["guide_retrieval_binding_manifest_id", "guide_retrieval_binding_manifest_hash"],
        ["id", "manifest_hash"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    connection = op.get_bind()
    row_count = connection.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
    if row_count:
        raise RuntimeError("Refusing to downgrade guide retrieval binding manifest with existing authority data")
    op.drop_constraint(
        "fk_ai_job_execution_context_guide_retrieval_binding",
        "ai_job_execution_context",
        type_="foreignkey",
    )
    op.drop_constraint(
        "chk_ai_job_execution_context_guide_retrieval_binding_domain",
        "ai_job_execution_context",
        type_="check",
    )
    op.drop_constraint(
        "chk_ai_job_execution_context_guide_retrieval_binding_pair",
        "ai_job_execution_context",
        type_="check",
    )
    op.drop_column("ai_job_execution_context", "guide_retrieval_binding_manifest_hash")
    op.drop_column("ai_job_execution_context", "guide_retrieval_binding_manifest_id")
    op.drop_table(TABLE)
