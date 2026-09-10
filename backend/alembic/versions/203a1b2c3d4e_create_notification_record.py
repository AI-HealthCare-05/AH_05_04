"""Create occurrence notifications (PD-203).

Revision ID: 203a1b2c3d4e
Revises: 165a0b1c2d3e
"""

import sqlalchemy as sa
from alembic import op

revision = "203a1b2c3d4e"
down_revision = "165a0b1c2d3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_record",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("occurrence_id", sa.CHAR(36), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["occurrence_id"], ["medication_occurrence.id"], ondelete="CASCADE", name="fk_notification_occurrence"
        ),
        sa.UniqueConstraint("occurrence_id", "kind", name="uq_notification_occurrence_kind"),
        sa.CheckConstraint("kind IN ('SCHEDULED', 'REMINDER')", name="chk_notification_kind"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND attempt = 0 AND cancelled_at IS NULL AND delivered_at IS NULL AND read_at IS NULL) OR "
            "(status = 'DELIVERED' AND attempt = 1 AND cancelled_at IS NULL AND delivered_at IS NOT NULL) OR "
            "(status = 'CANCELLED' AND attempt = 0 AND cancelled_at IS NOT NULL AND delivered_at IS NULL AND read_at IS NULL)",
            name="chk_notification_state",
        ),
        sa.CheckConstraint("read_at IS NULL OR read_at >= delivered_at", name="chk_notification_read_at"),
    )
    op.create_index("idx_notification_status_scheduled", "notification_record", ["status", "scheduled_at"])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE notification_record IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT count(*) FROM notification_record")).scalar_one():
        raise RuntimeError("notification history blocks downgrade; preserve records before rollback")
    op.drop_index("idx_notification_status_scheduled", table_name="notification_record")
    op.drop_table("notification_record")
