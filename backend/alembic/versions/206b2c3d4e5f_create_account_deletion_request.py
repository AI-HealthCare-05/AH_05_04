"""create account deletion request table

Revision ID: 206b2c3d4e5f
Revises: 583a1b2c3d4f
Create Date: 2026-09-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "206b2c3d4e5f"
down_revision: str | Sequence[str] | None = "583a1b2c3d4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_account_deletion_request_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    connection.execute(sa.text("LOCK TABLE account_deletion_request IN ACCESS EXCLUSIVE MODE"))
    count = connection.execute(sa.text("SELECT count(*) FROM account_deletion_request")).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade revision 206b2c3d4e5f while rows exist (account_deletion_request={count}). "
            "Use a forward-fix migration or an approved backup and data-retention rollback procedure instead."
        )


def upgrade() -> None:
    op.create_table(
        "account_deletion_request",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "IN_PROGRESS",
                "COMPLETED",
                "FAILED",
                name="accountdeletionrequeststatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED')",
            name="chk_account_deletion_request_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="chk_account_deletion_request_retry_count"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_account_deletion_request_user"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_account_deletion_request_active_per_user",
        "account_deletion_request",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'IN_PROGRESS', 'FAILED')"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    _ensure_account_deletion_request_downgrade_is_data_safe(bind)

    op.drop_index("uq_account_deletion_request_active_per_user", table_name="account_deletion_request")
    op.drop_table("account_deletion_request")
