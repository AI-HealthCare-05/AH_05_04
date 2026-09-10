"""Expand Python prescription fingerprints while preserving legacy guards.

Revision ID: 398a1b2c3d4e
Revises: 201a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

from provider_contracts.prescription_integrity_v1 import MEDICATION_CONTENT_FIELDS, prescription_fingerprint

revision = "398a1b2c3d4e"
down_revision = "201a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE prescription, prescription_version, prescription_version_medication IN ACCESS EXCLUSIVE MODE"
        )
    )
    op.add_column("prescription_version", sa.Column("medication_count", sa.Integer(), nullable=True))
    op.add_column("prescription_version", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("prescription_version_medication", sa.Column("medication_count", sa.Integer(), nullable=True))

    # Only suspend the existing UPDATE guards while holding exclusive locks.
    # Transaction rollback restores both data/schema and original guard state on failure.
    guards = connection.execute(
        sa.text(
            "SELECT c.relname, t.tgname, t.tgenabled::text FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() "
            "AND t.tgname IN ('trg_prescription_version_prevent_update', "
            "'trg_prescription_version_medication_prevent_update')"
        )
    ).all()
    for table, trigger, _enabled in guards:
        connection.execute(sa.text(f'ALTER TABLE "{table}" DISABLE TRIGGER "{trigger}"'))
    columns = ", ".join(MEDICATION_CONTENT_FIELDS)
    versions = connection.execute(sa.text("SELECT id, prescribed_date FROM prescription_version ORDER BY id")).all()
    for version_id, prescribed_date in versions:
        rows = (
            connection.execute(
                sa.text(f"SELECT {columns} FROM prescription_version_medication WHERE prescription_version_id=:id"),
                {"id": version_id},
            )
            .mappings()
            .all()
        )
        try:
            fingerprint = prescription_fingerprint(prescribed_date, [dict(row) for row in rows])
        except ValueError:
            raise RuntimeError(
                "Prescription fingerprint backfill refused: invalid existing medication content"
            ) from None
        values = {"id": version_id, "count": fingerprint.medication_count, "hash": fingerprint.content_hash}
        connection.execute(
            sa.text("UPDATE prescription_version SET medication_count=:count, content_hash=:hash WHERE id=:id"), values
        )
        connection.execute(
            sa.text(
                "UPDATE prescription_version_medication SET medication_count=:count WHERE prescription_version_id=:id"
            ),
            values,
        )
    for table, trigger, enabled in guards:
        mode = {"O": "ENABLE", "D": "DISABLE", "R": "ENABLE REPLICA", "A": "ENABLE ALWAYS"}[enabled]
        connection.execute(sa.text(f'ALTER TABLE "{table}" {mode} TRIGGER "{trigger}"'))

    op.create_check_constraint(
        "chk_prescription_version_medication_count", "prescription_version", "medication_count > 0"
    )
    op.create_check_constraint(
        "chk_prescription_version_content_hash", "prescription_version", "content_hash ~ '^[0-9a-f]{64}$'"
    )
    op.create_check_constraint(
        "chk_prescription_version_seal_pair",
        "prescription_version",
        "(medication_count IS NULL) = (content_hash IS NULL)",
    )
    op.create_unique_constraint("uq_prescription_version_id_count", "prescription_version", ["id", "medication_count"])
    op.create_check_constraint(
        "chk_prescription_medication_slot",
        "prescription_version_medication",
        "medication_count > 0 AND display_order BETWEEN 1 AND medication_count",
    )
    op.create_foreign_key(
        "fk_prescription_medication_version_count",
        "prescription_version_medication",
        "prescription_version",
        ["prescription_version_id", "medication_count"],
        ["id", "medication_count"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE prescription, prescription_version, prescription_version_medication IN ACCESS EXCLUSIVE MODE"
        )
    )
    guards = connection.execute(
        sa.text(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() "
            "AND t.tgenabled IN ('O', 'A') AND t.tgname IN "
            "('trg_prescription_version_prevent_update', 'trg_prescription_version_medication_prevent_update')"
        )
    ).scalar_one()
    if guards != 2:
        raise RuntimeError("Restore legacy protection through a forward fix before removing fingerprint metadata")
    op.drop_constraint(
        "fk_prescription_medication_version_count", "prescription_version_medication", type_="foreignkey"
    )
    op.drop_constraint("chk_prescription_medication_slot", "prescription_version_medication", type_="check")
    op.drop_constraint("uq_prescription_version_id_count", "prescription_version", type_="unique")
    for name in (
        "chk_prescription_version_medication_count",
        "chk_prescription_version_content_hash",
        "chk_prescription_version_seal_pair",
    ):
        op.drop_constraint(name, "prescription_version", type_="check")
    op.drop_column("prescription_version_medication", "medication_count")
    op.drop_column("prescription_version", "content_hash")
    op.drop_column("prescription_version", "medication_count")
