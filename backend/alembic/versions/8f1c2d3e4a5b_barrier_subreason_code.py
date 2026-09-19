"""Persist the Track C barrier subreason so the clinic report can show it.

The column is free-form text rather than an enum: the approved vocabulary lives in
track_c_personalization.SUBREASONS and is validated on every write, so adding a
subreason does not require a schema change.

Revision ID: 8f1c2d3e4a5b
Revises: 806a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

revision = "8f1c2d3e4a5b"
down_revision = "806a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("barrier_response", sa.Column("subreason_code", sa.String(length=100), nullable=True))
    op.create_check_constraint(
        "chk_barrier_subreason_requires_code",
        "barrier_response",
        "subreason_code IS NULL OR barrier_code IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("chk_barrier_subreason_requires_code", "barrier_response", type_="check")
    op.drop_column("barrier_response", "subreason_code")
