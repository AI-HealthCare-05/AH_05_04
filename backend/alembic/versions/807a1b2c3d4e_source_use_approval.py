"""Persist production Source Use Approval authority facts (#807)."""

import sqlalchemy as sa
from alembic import op

revision = "807a1b2c3d4e"
down_revision = "8f1c2d3e4a5b"
branch_labels = None
depends_on = None

_TABLE = "rag_source_use_approval"
_REVOCATION_SHAPE = (
    "(revoked_at IS NULL AND revoked_by IS NULL AND revoked_reason IS NULL) OR "
    "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_reason IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_code", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("approval_version", sa.String(length=120), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.CHAR(length=36), nullable=True),
        sa.Column("revoked_reason", sa.String(length=200), nullable=True),
        sa.Column("actor_id", sa.CHAR(length=36), nullable=False),
        sa.Column("evidence_ref", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_rag_source_use_approval"),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "environment",
            "purpose",
            "approval_version",
            name="uq_rag_source_use_approval_identity",
        ),
        sa.UniqueConstraint(
            "id",
            "source_snapshot_id",
            "source_version",
            name="uq_rag_source_use_approval_id_target",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_rag_source_use_approval_snapshot_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["user.id"],
            name="fk_rag_source_use_approval_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["user.id"],
            name="fk_rag_source_use_approval_revoked_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "environment IN ('LOCAL', 'TEST', 'CLOSED_DEMO', 'PRODUCTION')",
            name="chk_rag_source_use_approval_environment",
        ),
        sa.CheckConstraint(
            "purpose IN ('PRODUCT_IDENTIFICATION', 'SAFETY_ROUTING', 'RULE_DERIVATION', 'RETRIEVAL', 'PATIENT_CITATION')",
            name="chk_rag_source_use_approval_purpose",
        ),
        sa.CheckConstraint(
            "length(trim(source_code)) > 0",
            name="chk_rag_source_use_approval_source_code_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_source_use_approval_source_version_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(approval_version)) > 0",
            name="chk_rag_source_use_approval_version_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(evidence_ref)) > 0",
            name="chk_rag_source_use_approval_evidence_nonblank",
        ),
        sa.CheckConstraint(
            "expires_at > valid_from",
            name="chk_rag_source_use_approval_validity_window",
        ),
        sa.CheckConstraint(_REVOCATION_SHAPE, name="chk_rag_source_use_approval_revocation_shape"),
    )
    op.create_index("idx_rag_source_use_approval_snapshot", _TABLE, ["source_snapshot_id"])


def _has_rows(connection: sa.Connection) -> bool:
    return bool(connection.execute(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")).first())


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    if _has_rows(bind):
        raise RuntimeError("Refusing to downgrade Source Use Approval records with existing data")
    op.drop_index("idx_rag_source_use_approval_snapshot", table_name=_TABLE)
    op.drop_table(_TABLE)
