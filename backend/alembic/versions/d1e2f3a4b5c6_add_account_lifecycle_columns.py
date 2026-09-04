"""add account lifecycle columns to user

Revision ID: d1e2f3a4b5c6
Revises: 2a9f4c7d8e10
Create Date: 2026-09-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "2a9f4c7d8e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PD-206 결정 1: 기존 계정은 전부 ACTIVE·token_version=0으로 시작합니다.
    op.add_column(
        "user",
        sa.Column(
            "account_status",
            sa.Enum(
                "ACTIVE",
                "WITHDRAWAL_REQUESTED",
                "WITHDRAWN",
                name="accountstatus",
                native_enum=False,
                length=25,
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
    )
    op.add_column("user", sa.Column("withdrawal_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "user",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("user", "token_version")
    op.drop_column("user", "withdrawn_at")
    op.drop_column("user", "withdrawal_requested_at")
    op.drop_column("user", "account_status")
