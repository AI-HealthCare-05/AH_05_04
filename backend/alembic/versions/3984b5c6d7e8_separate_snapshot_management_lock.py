"""Separate management row-lock permission from Snapshot provenance (PD-398-R2).

Revision ID: 3984b5c6d7e8
Revises: 3983a4b5c6d7
"""

import sqlalchemy as sa
from alembic import op

revision = "3984b5c6d7e8"
down_revision = "3983a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rag_source_snapshot", sa.Column("management_lock_marker", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_check_constraint(
        "chk_rag_source_snapshot_management_lock_marker", "rag_source_snapshot", "management_lock_marker = 0"
    )


def downgrade() -> None:
    raise RuntimeError("Snapshot management lock isolation cannot be downgraded; use a reviewed forward-fix")
