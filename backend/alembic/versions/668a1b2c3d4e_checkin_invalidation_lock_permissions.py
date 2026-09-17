"""Preserve Track C history while permitting Check-in invalidation row locks (#668)."""

import sqlalchemy as sa
from alembic import op

revision = "668a1b2c3d4e"
down_revision = "633a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, prefix in (("safety_assessment", "safety"), ("barrier_response", "barrier")):
        op.add_column(table, sa.Column("checkin_lock_marker", sa.Integer(), server_default="0", nullable=False))
        op.create_check_constraint(f"chk_{prefix}_checkin_lock_marker", table, "checkin_lock_marker = 0")


def downgrade() -> None:
    for table, prefix in (("barrier_response", "barrier"), ("safety_assessment", "safety")):
        op.drop_constraint(f"chk_{prefix}_checkin_lock_marker", table, type_="check")
        op.drop_column(table, "checkin_lock_marker")
