"""Pin exact PATIENT_CITATION approvals into canonical Runtime Bundles (#853).

Revision ID: 853a1b2c3d4e
Revises: 807a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

revision = "853a1b2c3d4e"
down_revision = "807a1b2c3d4e"
branch_labels = None
depends_on = None

_TABLE = "rag_runtime_bundle_citation_approval"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("bundle_id", sa.CHAR(length=36), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_use_approval_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_code", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=False),
        sa.Column("approval_version", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_rag_runtime_bundle_citation_approval"),
        sa.UniqueConstraint(
            "bundle_id",
            "source_snapshot_id",
            name="uq_rag_runtime_bundle_citation_approval_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["bundle_id", "bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_runtime_bundle_citation_approval_bundle_manifest",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_runtime_bundle_citation_approval_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_use_approval_id"],
            ["rag_source_use_approval.id"],
            name="fk_rag_runtime_bundle_citation_approval_source_use_approval",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "purpose = 'PATIENT_CITATION'",
            name="chk_rag_runtime_bundle_citation_approval_purpose",
        ),
        sa.CheckConstraint(
            "environment IN ('LOCAL', 'TEST', 'CLOSED_DEMO', 'PRODUCTION')",
            name="chk_rag_runtime_bundle_citation_approval_environment",
        ),
        sa.CheckConstraint(
            "length(bundle_manifest_hash) = 64",
            name="chk_rag_runtime_bundle_citation_approval_manifest_hash",
        ),
        sa.CheckConstraint(
            "length(trim(source_code)) > 0",
            name="chk_rag_runtime_bundle_citation_approval_source_code",
        ),
        sa.CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_runtime_bundle_citation_approval_source_version",
        ),
        sa.CheckConstraint(
            "length(trim(approval_version)) > 0",
            name="chk_rag_runtime_bundle_citation_approval_version",
        ),
    )
    op.create_index(
        "idx_rag_runtime_bundle_citation_approval_bundle_manifest",
        _TABLE,
        ["bundle_id", "bundle_manifest_hash"],
    )
    op.create_index(
        "idx_rag_runtime_bundle_citation_approval_source_use_approval",
        _TABLE,
        ["source_use_approval_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE})")).scalar_one():
        raise RuntimeError("refusing destructive downgrade: Runtime Bundle PATIENT_CITATION approval pins exist")
    op.drop_index("idx_rag_runtime_bundle_citation_approval_source_use_approval", table_name=_TABLE)
    op.drop_index("idx_rag_runtime_bundle_citation_approval_bundle_manifest", table_name=_TABLE)
    op.drop_table(_TABLE)
