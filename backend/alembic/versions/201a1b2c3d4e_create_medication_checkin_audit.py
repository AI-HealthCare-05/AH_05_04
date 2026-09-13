"""create medication check-in and audit tables

Revision ID: 201a1b2c3d4e
Revises: 164c5d6e7f8a
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "201a1b2c3d4e"
down_revision: str | Sequence[str] | None = "164c5d6e7f8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "medication_checkin",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("occurrence_id", sa.CHAR(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('TAKEN', 'NOT_TAKEN', 'UNCONFIRMED')",
            name="chk_medication_checkin_status",
        ),
        sa.CheckConstraint("revision > 0", name="chk_medication_checkin_revision"),
        sa.CheckConstraint(
            "status = 'TAKEN' OR taken_at IS NULL",
            name="chk_medication_checkin_taken_at_status",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["medication_occurrence.id"],
            name="fk_medication_checkin_occurrence",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("occurrence_id", name="uq_medication_checkin_occurrence_id"),
    )
    op.create_index(
        "idx_medication_checkin_status_updated",
        "medication_checkin",
        ["status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "checkin_audit",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("checkin_id", sa.CHAR(length=36), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.CHAR(length=36), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "from_status IN ('TAKEN', 'NOT_TAKEN', 'UNCONFIRMED')",
            name="chk_checkin_audit_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('TAKEN', 'NOT_TAKEN', 'UNCONFIRMED')",
            name="chk_checkin_audit_to_status",
        ),
        sa.CheckConstraint("from_revision > 0", name="chk_checkin_audit_from_revision"),
        sa.CheckConstraint("to_revision = from_revision + 1", name="chk_checkin_audit_revision_step"),
        sa.ForeignKeyConstraint(
            ["checkin_id"],
            ["medication_checkin.id"],
            name="fk_checkin_audit_checkin",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"],
            ["user.id"],
            name="fk_checkin_audit_changed_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkin_id", "to_revision", name="uq_checkin_audit_checkin_to_revision"),
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION prevent_checkin_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'checkin_audit rows are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER trg_checkin_audit_append_only
        BEFORE UPDATE OR DELETE ON checkin_audit
        FOR EACH ROW EXECUTE FUNCTION prevent_checkin_audit_mutation();
        """)
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE medication_checkin, checkin_audit IN ACCESS EXCLUSIVE MODE"))
    counts = {
        table: connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in ("medication_checkin", "checkin_audit")
    }
    populated = {table: count for table, count in counts.items() if count}
    if populated:
        summary = ", ".join(f"{table}={count}" for table, count in populated.items())
        raise RuntimeError(f"Cannot downgrade revision {revision}: Check-in history exists ({summary}).")

    op.execute(sa.text("DROP TRIGGER trg_checkin_audit_append_only ON checkin_audit"))
    op.execute(sa.text("DROP FUNCTION prevent_checkin_audit_mutation()"))
    op.drop_table("checkin_audit")
    op.drop_index("idx_medication_checkin_status_updated", table_name="medication_checkin")
    op.drop_table("medication_checkin")
