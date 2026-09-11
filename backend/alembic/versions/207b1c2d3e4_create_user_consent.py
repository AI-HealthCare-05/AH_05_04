"""create purpose-scoped user consent table

Revision ID: 207b1c2d3e4
Revises: 428a1b2c3d4e
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "207b1c2d3e4"
down_revision: str | Sequence[str] | None = "428a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _raise_if_user_consent_exists() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE user_consent IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT 1 FROM user_consent LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade user_consent while consent rows exist")


def upgrade() -> None:
    op.create_table(
        "user_consent",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "purpose IN ('OCR', 'GUIDE', 'CHAT', 'NOTIFICATION')",
            name="chk_user_consent_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('GRANTED', 'WITHDRAWN')",
            name="chk_user_consent_status",
        ),
        sa.CheckConstraint(
            "length(policy_version) > 0",
            name="chk_user_consent_policy_version_non_empty",
        ),
        sa.CheckConstraint(
            "(status = 'GRANTED' AND granted_at IS NOT NULL AND withdrawn_at IS NULL) OR "
            "(status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL)",
            name="chk_user_consent_status_timestamps",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_user_consent_user"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "purpose", name="uq_user_consent_user_purpose"),
    )
    op.create_index("idx_user_consent_user_status", "user_consent", ["user_id", "status"])


def downgrade() -> None:
    _raise_if_user_consent_exists()
    op.drop_index("idx_user_consent_user_status", table_name="user_consent")
    op.drop_table("user_consent")
