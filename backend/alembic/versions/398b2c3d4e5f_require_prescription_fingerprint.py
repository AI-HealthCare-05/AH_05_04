"""Require complete prescription fingerprints after consumer cutover.

Revision ID: 398b2c3d4e5f
Revises: 398a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

from provider_contracts.prescription_integrity import MEDICATION_CONTENT_FIELDS, verify_prescription_fingerprint

revision = "398b2c3d4e5f"
down_revision = "398a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE prescription, prescription_version, prescription_version_medication IN ACCESS EXCLUSIVE MODE"
        )
    )
    columns = ", ".join(MEDICATION_CONTENT_FIELDS)
    for version in (
        connection.execute(
            sa.text("SELECT id, prescribed_date, medication_count, content_hash FROM prescription_version ORDER BY id")
        )
        .mappings()
        .all()
    ):
        rows = (
            connection.execute(
                sa.text(
                    f"SELECT {columns}, medication_count FROM prescription_version_medication WHERE prescription_version_id=:id"
                ),
                {"id": version["id"]},
            )
            .mappings()
            .all()
        )
        try:
            verify_prescription_fingerprint(
                version["prescribed_date"],
                [dict(row) for row in rows],
                medication_count=version["medication_count"],
                content_hash=version["content_hash"],
            )
        except ValueError:
            raise RuntimeError("Prescription NOT NULL cutover refused: missing or invalid fingerprint") from None
    op.alter_column("prescription_version", "medication_count", existing_type=sa.Integer(), nullable=False)
    op.alter_column("prescription_version", "content_hash", existing_type=sa.String(64), nullable=False)
    op.alter_column("prescription_version_medication", "medication_count", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.alter_column("prescription_version_medication", "medication_count", existing_type=sa.Integer(), nullable=True)
    op.alter_column("prescription_version", "content_hash", existing_type=sa.String(64), nullable=True)
    op.alter_column("prescription_version", "medication_count", existing_type=sa.Integer(), nullable=True)
