"""create prescription version foundation

Revision ID: 169a1b2c3d4e
Revises: 164f3a2b1c0d
Create Date: 2026-09-07

This is the Expand slice for Issue #169. It adds only schema and ORM support;
legacy rows are intentionally not backfilled and application reads/writes are
not switched in this revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "169a1b2c3d4e"
down_revision: str | Sequence[str] | None = "164f3a2b1c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION_TABLES = (
    "prescription_version_medication",
    "prescription_version",
)


def _ensure_downgrade_is_data_safe(connection: sa.engine.Connection) -> None:
    """Prevent a downgrade from deleting immutable prescription history."""
    connection.execute(sa.text("LOCK TABLE prescription IN ACCESS EXCLUSIVE MODE"))
    for table_name in _VERSION_TABLES:
        connection.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))

    non_empty_tables = [
        table_name
        for table_name in _VERSION_TABLES
        if connection.execute(sa.text(f"SELECT count(*) FROM {table_name}")).scalar_one()
    ]
    if non_empty_tables:
        raise RuntimeError(
            "Cannot downgrade revision 169a1b2c3d4e while prescription version data exists. "
            f"Non-empty tables: {', '.join(non_empty_tables)}. Use a forward-fix migration or an "
            "approved backup and data-retention rollback procedure instead."
        )


def _create_immutability_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_prescription_version_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                    RETURN OLD;
                END IF;

                RAISE EXCEPTION '% rows are immutable; direct mutation is forbidden', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    for table_name in _VERSION_TABLES:
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_prevent_update
                BEFORE UPDATE ON {table_name}
                FOR EACH ROW
                EXECUTE FUNCTION prevent_prescription_version_mutation()
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_prevent_delete
                BEFORE DELETE ON {table_name}
                FOR EACH ROW
                EXECUTE FUNCTION prevent_prescription_version_mutation()
                """
            )
        )


def _create_membership_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_frozen_prescription_version_medication_insert()
            RETURNS trigger AS $$
            DECLARE
                parent_prescription_id char(36);
                active_version_id char(36);
                prescription_xmin bigint;
                version_xmin bigint;
            BEGIN
                SELECT prescription_id, xmin::text::bigint
                INTO parent_prescription_id, version_xmin
                FROM prescription_version
                WHERE id = NEW.prescription_version_id;

                IF NOT FOUND THEN
                    RETURN NEW;
                END IF;

                SELECT prescription.active_version_id, prescription.xmin::text::bigint
                INTO active_version_id, prescription_xmin
                FROM prescription
                WHERE id = parent_prescription_id
                FOR UPDATE;

                -- A version activation transaction may assemble its medication set before
                -- commit. Once that prescription tuple is committed, active and superseded
                -- versions reject all further membership changes.
                IF active_version_id = NEW.prescription_version_id
                   AND prescription_xmin = txid_current()
                   AND version_xmin = txid_current() THEN
                    RETURN NEW;
                END IF;

                IF active_version_id = NEW.prescription_version_id
                   OR EXISTS (
                       SELECT 1
                       FROM prescription_version AS newer_version
                       JOIN prescription_version AS target_version
                         ON target_version.id = NEW.prescription_version_id
                       WHERE newer_version.prescription_id = target_version.prescription_id
                         AND newer_version.version_number > target_version.version_number
                   ) THEN
                    RAISE EXCEPTION
                        'prescription version medication set is frozen; create a new version instead'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'chk_prescription_version_medication_frozen';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_prescription_version_medication_prevent_frozen_insert
            BEFORE INSERT ON prescription_version_medication
            FOR EACH ROW
            EXECUTE FUNCTION prevent_frozen_prescription_version_medication_insert()
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION check_prescription_version_medications()
            RETURNS trigger AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM prescription_version_medication
                    WHERE prescription_version_id = NEW.id
                ) THEN
                    RAISE EXCEPTION 'prescription version requires at least one medication'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'chk_prescription_version_medication';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE CONSTRAINT TRIGGER trg_prescription_version_medication_required
            AFTER INSERT ON prescription_version
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION check_prescription_version_medications()
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION check_prescription_active_version_medications()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.active_version_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1
                       FROM prescription_version_medication
                       WHERE prescription_version_id = NEW.active_version_id
                   ) THEN
                    RAISE EXCEPTION 'active prescription version requires at least one medication'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'chk_prescription_active_version_medication';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE CONSTRAINT TRIGGER trg_prescription_active_version_medication
            AFTER INSERT OR UPDATE ON prescription
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION check_prescription_active_version_medications()
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "prescription_version",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_id", sa.CHAR(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("prescribed_date", sa.Date(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version_number > 0", name="chk_prescription_version_number"),
        sa.ForeignKeyConstraint(
            ["prescription_id"],
            ["prescription.id"],
            name="fk_prescription_version_prescription",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "prescription_id", name="uq_prescription_version_id_prescription"),
        sa.UniqueConstraint(
            "prescription_id",
            "version_number",
            name="uq_prescription_version_number",
        ),
    )
    op.create_index(
        "idx_prescription_version_prescription_created",
        "prescription_version",
        ["prescription_id", "created_at", "id"],
    )

    op.create_table(
        "prescription_version_medication",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_version_id", sa.CHAR(length=36), nullable=False),
        sa.Column("medication_name", sa.String(length=255), nullable=False),
        sa.Column("strength_text", sa.String(length=100), nullable=True),
        sa.Column("dose_value", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("dose_unit", sa.String(length=50), nullable=True),
        sa.Column("frequency_per_day", sa.Integer(), nullable=True),
        sa.Column("timing_text", sa.String(length=255), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "dose_value IS NULL OR dose_value > 0",
            name="chk_prescription_version_medication_dose",
        ),
        sa.CheckConstraint(
            "frequency_per_day IS NULL OR frequency_per_day > 0",
            name="chk_prescription_version_medication_frequency",
        ),
        sa.CheckConstraint(
            "duration_days IS NULL OR duration_days > 0",
            name="chk_prescription_version_medication_duration",
        ),
        sa.CheckConstraint(
            "display_order > 0",
            name="chk_prescription_version_medication_display_order",
        ),
        sa.CheckConstraint(
            "length(trim(medication_name)) > 0",
            name="chk_prescription_version_medication_name_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["prescription_version_id"],
            ["prescription_version.id"],
            name="fk_prescription_version_medication_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prescription_version_id",
            "display_order",
            name="uq_prescription_version_medication_order",
        ),
    )
    op.create_index(
        "idx_prescription_version_medication_version",
        "prescription_version_medication",
        ["prescription_version_id", "display_order", "id"],
    )

    # Existing prescriptions deliberately remain NULL until the PR 2 backfill.
    op.add_column("prescription", sa.Column("active_version_id", sa.CHAR(length=36), nullable=True))
    op.create_foreign_key(
        "fk_prescription_active_version",
        "prescription",
        "prescription_version",
        ["active_version_id", "id"],
        ["id", "prescription_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index("idx_prescription_active_version", "prescription", ["active_version_id"])

    _create_immutability_guards()
    _create_membership_guards()


def downgrade() -> None:
    _ensure_downgrade_is_data_safe(op.get_bind())

    op.execute("DROP TRIGGER IF EXISTS trg_prescription_active_version_medication ON prescription")
    op.execute("DROP FUNCTION IF EXISTS check_prescription_active_version_medications()")
    op.execute("DROP TRIGGER IF EXISTS trg_prescription_version_medication_required ON prescription_version")
    op.execute("DROP FUNCTION IF EXISTS check_prescription_version_medications()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prescription_version_medication_prevent_frozen_insert "
        "ON prescription_version_medication"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_frozen_prescription_version_medication_insert()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prescription_version_medication_prevent_delete ON prescription_version_medication"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prescription_version_medication_prevent_update ON prescription_version_medication"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_prescription_version_prevent_delete ON prescription_version")
    op.execute("DROP TRIGGER IF EXISTS trg_prescription_version_prevent_update ON prescription_version")
    op.execute("DROP FUNCTION IF EXISTS prevent_prescription_version_mutation()")

    op.drop_index("idx_prescription_active_version", table_name="prescription")
    op.drop_constraint("fk_prescription_active_version", "prescription", type_="foreignkey")
    op.drop_column("prescription", "active_version_id")
    op.drop_index(
        "idx_prescription_version_medication_version",
        table_name="prescription_version_medication",
    )
    op.drop_table("prescription_version_medication")
    op.drop_index("idx_prescription_version_prescription_created", table_name="prescription_version")
    op.drop_table("prescription_version")
