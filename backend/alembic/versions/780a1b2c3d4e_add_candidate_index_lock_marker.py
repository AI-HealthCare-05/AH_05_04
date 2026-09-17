"""Add lock-only marker column to rag_candidate_index_version (#780).

Revision ID: 780a1b2c3d4e
Revises: 712a1b2c3d4e
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op

revision = "780a1b2c3d4e"
down_revision = "712a1b2c3d4e"
branch_labels = None
depends_on = None

_TABLE_NAME = "rag_candidate_index_version"
_CONSTRAINT_NAME = "chk_rag_candidate_index_lock_marker"
_COLUMN_NAME = "candidate_index_lock_marker"


def upgrade() -> None:
    op.add_column(
        _TABLE_NAME,
        sa.Column(_COLUMN_NAME, sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE_NAME,
        f"{_COLUMN_NAME} = 0",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE_NAME, type_="check")
    op.drop_column(_TABLE_NAME, _COLUMN_NAME)
