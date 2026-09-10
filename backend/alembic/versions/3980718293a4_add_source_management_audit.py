"""Add explicit Source/Catalog management permission and audit storage.

Revision ID: 3980718293a4
Revises: 398f60718293
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "3980718293a4"
down_revision = "398f60718293"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_management_permission",
        sa.Column("user_id", sa.CHAR(36), sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "source_management_audit",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.CHAR(36), nullable=False),
        sa.Column("operation", sa.String(8), nullable=False),
        sa.Column("actor_id", sa.CHAR(36), nullable=False),
        sa.Column("permission", sa.String(40), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("request_id", sa.CHAR(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("before_revision", sa.Integer(), nullable=False),
        sa.Column("after_revision", sa.Integer(), nullable=True),
        sa.Column("before_hash", sa.String(64), nullable=False),
        sa.Column("after_hash", sa.String(64), nullable=True),
        sa.Column("before_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("after_provenance", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("actor_id", "request_id", name="uq_source_management_actor_request"),
        sa.UniqueConstraint("target_kind", "target_id", "after_revision", name="uq_source_management_target_revision"),
        sa.CheckConstraint("operation IN ('UPDATE','DELETE','GRANT','REVOKE')", name="ck_source_management_operation"),
        sa.CheckConstraint("before_revision >= 0", name="ck_source_management_before_revision"),
    )


def downgrade() -> None:
    # Permission removal must not silently destroy management evidence.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM source_management_audit)")):
        raise RuntimeError("Management audit exists; preserve evidence before downgrade")
    op.drop_table("source_management_audit")
    op.drop_table("source_management_permission")
