"""add refresh token rotation column and password reset token table

Revision ID: 206a1b2c3d4e
Revises: 164c5d6e7f8a
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "206a1b2c3d4e"
down_revision: str | Sequence[str] | None = "164c5d6e7f8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_password_reset_token_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    connection.execute(sa.text("LOCK TABLE password_reset_token IN ACCESS EXCLUSIVE MODE"))
    row_count = connection.execute(sa.text("SELECT count(*) FROM password_reset_token")).scalar_one()
    if row_count:
        raise RuntimeError(
            "Cannot downgrade revision 206a1b2c3d4e while password_reset_token rows exist "
            f"({row_count} rows). Use a forward-fix migration or an approved backup and "
            "data-retention rollback procedure instead."
        )


def upgrade() -> None:
    op.add_column("user", sa.Column("active_refresh_jti", sa.String(length=32), nullable=True))

    op.create_table(
        "password_reset_token",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(token_hash) = 64", name="chk_password_reset_token_hash_length"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_password_reset_token_user"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_password_reset_token_token_hash",
        "password_reset_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "idx_password_reset_token_user_created",
        "password_reset_token",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    _ensure_password_reset_token_downgrade_is_data_safe(bind)

    op.drop_index("idx_password_reset_token_user_created", table_name="password_reset_token")
    op.drop_index("ix_password_reset_token_token_hash", table_name="password_reset_token")
    op.drop_table("password_reset_token")
    op.drop_column("user", "active_refresh_jti")
