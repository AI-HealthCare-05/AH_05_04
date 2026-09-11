"""create email verification token table

Revision ID: 431a1b2c3d4e
Revises: 203a1b2c3d4e
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "431a1b2c3d4e"
down_revision: str | Sequence[str] | None = "203a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_email_verification_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    connection.execute(sa.text("LOCK TABLE email_verification_token IN ACCESS EXCLUSIVE MODE"))
    count = connection.execute(sa.text("SELECT count(*) FROM email_verification_token")).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade revision 431a1b2c3d4e while rows exist (email_verification_token={count}). "
            "Use a forward-fix migration or an approved backup and data-retention rollback procedure instead."
        )


def upgrade() -> None:
    op.create_table(
        "email_verification_token",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("email", sa.String(length=40), nullable=False),
        sa.Column(
            "purpose", sa.Enum("SIGNUP", name="emailverificationpurpose", native_enum=False, length=30), nullable=False
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(token_hash) = 64", name="chk_email_verification_token_hash_length"),
        sa.CheckConstraint("length(trim(email)) > 0", name="chk_email_verification_token_email_nonblank"),
        sa.CheckConstraint("purpose IN ('SIGNUP')", name="chk_email_verification_token_purpose"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_verification_token_token_hash",
        "email_verification_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "idx_email_verification_token_email_purpose_created",
        "email_verification_token",
        ["email", "purpose", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    _ensure_email_verification_downgrade_is_data_safe(bind)

    op.drop_index("idx_email_verification_token_email_purpose_created", table_name="email_verification_token")
    op.drop_index("ix_email_verification_token_token_hash", table_name="email_verification_token")
    op.drop_table("email_verification_token")
