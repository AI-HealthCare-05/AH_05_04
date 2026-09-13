"""create medication schedule and occurrence tables

Revision ID: 199a1b2c3d4e
Revises: 169d4e5f6a7b
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "199a1b2c3d4e"
down_revision: str | Sequence[str] | None = "169d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "medication_schedule",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_version_medication_id", sa.CHAR(length=36), nullable=False),
        sa.Column("start_local_date", sa.Date(), nullable=False),
        sa.Column("end_mode", sa.String(length=20), nullable=False),
        sa.Column("end_local_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision > 0", name="chk_medication_schedule_revision"),
        sa.CheckConstraint("end_mode IN ('DATE', 'OPEN_ENDED')", name="chk_medication_schedule_end_mode"),
        sa.CheckConstraint(
            "source IN ('PRESCRIPTION_EXACT', 'USER_CONFIRMED')",
            name="chk_medication_schedule_source",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'CANCELLED', 'ENDED')",
            name="chk_medication_schedule_status",
        ),
        sa.CheckConstraint(
            "(end_mode = 'DATE' AND end_local_date IS NOT NULL) "
            "OR (end_mode = 'OPEN_ENDED' AND end_local_date IS NULL)",
            name="chk_medication_schedule_end_mode_date",
        ),
        sa.CheckConstraint(
            "end_local_date IS NULL OR end_local_date >= start_local_date",
            name="chk_medication_schedule_date_range",
        ),
        sa.ForeignKeyConstraint(
            ["prescription_version_medication_id"],
            ["prescription_version_medication.id"],
            name="fk_medication_schedule_version_medication",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prescription_version_medication_id",
            name="uq_medication_schedule_version_medication",
        ),
    )
    op.create_table(
        "medication_schedule_time",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_schedule_id", sa.CHAR(length=36), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("local_time", sa.Time(timezone=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("schedule_revision > 0", name="chk_medication_schedule_time_revision"),
        sa.ForeignKeyConstraint(
            ["medication_schedule_id"],
            ["medication_schedule.id"],
            name="fk_medication_schedule_time_schedule",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "medication_schedule_id",
            "schedule_revision",
            "local_time",
            name="uq_medication_schedule_time_revision_local_time",
        ),
    )

    op.create_table(
        "medication_occurrence",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_schedule_id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_schedule_time_id", sa.CHAR(length=36), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("scheduled_local_date", sa.Date(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("schedule_revision > 0", name="chk_medication_occurrence_schedule_revision"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CANCELLED', 'CLOSED')",
            name="chk_medication_occurrence_status",
        ),
        sa.CheckConstraint(
            "confirmation_deadline_at >= scheduled_at",
            name="chk_medication_occurrence_deadline_after_schedule",
        ),
        sa.ForeignKeyConstraint(
            ["medication_schedule_id"],
            ["medication_schedule.id"],
            name="fk_medication_occurrence_schedule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medication_schedule_time_id"],
            ["medication_schedule_time.id"],
            name="fk_medication_occurrence_schedule_time",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "medication_schedule_time_id",
            "scheduled_local_date",
            name="uq_medication_occurrence_time_local_date",
        ),
    )
    op.create_index(
        "idx_medication_occurrence_schedule_date",
        "medication_occurrence",
        ["medication_schedule_id", "scheduled_local_date"],
        unique=False,
    )
    op.create_index(
        "idx_medication_occurrence_deadline_status",
        "medication_occurrence",
        ["confirmation_deadline_at", "status"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE medication_schedule, medication_schedule_time, medication_occurrence IN ACCESS EXCLUSIVE MODE"
        )
    )
    counts = {
        table: connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in ("medication_schedule", "medication_schedule_time", "medication_occurrence")
    }
    populated = {table: count for table, count in counts.items() if count}
    if populated:
        summary = ", ".join(f"{table}={count}" for table, count in populated.items())
        raise RuntimeError(f"Cannot downgrade revision {revision}: Track B history exists ({summary}).")

    op.drop_index("idx_medication_occurrence_deadline_status", table_name="medication_occurrence")
    op.drop_index("idx_medication_occurrence_schedule_date", table_name="medication_occurrence")
    op.drop_table("medication_occurrence")
    op.drop_table("medication_schedule_time")
    op.drop_table("medication_schedule")
