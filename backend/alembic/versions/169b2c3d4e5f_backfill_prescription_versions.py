"""backfill prescription versions

Revision ID: 169b2c3d4e5f
Revises: 165f90716263
Create Date: 2026-09-08

Backfill the immutable Version 1 snapshot for legacy prescriptions. The
application keeps reading the legacy tables during this dual-write slice.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "169b2c3d4e5f"
down_revision: str | Sequence[str] | None = "165f90716263"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_BATCH_SIZE = 500

_prescription_version = sa.table(
    "prescription_version",
    sa.column("id", sa.CHAR(length=36)),
    sa.column("prescription_id", sa.CHAR(length=36)),
    sa.column("version_number", sa.Integer()),
    sa.column("prescribed_date", sa.Date()),
    sa.column("confirmed_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

_prescription_version_medication = sa.table(
    "prescription_version_medication",
    sa.column("id", sa.CHAR(length=36)),
    sa.column("prescription_version_id", sa.CHAR(length=36)),
    sa.column("medication_name", sa.String(length=255)),
    sa.column("strength_text", sa.String(length=100)),
    sa.column("dose_value", sa.Numeric(precision=10, scale=3)),
    sa.column("dose_unit", sa.String(length=50)),
    sa.column("frequency_per_day", sa.Integer()),
    sa.column("timing_text", sa.String(length=255)),
    sa.column("duration_days", sa.Integer()),
    sa.column("display_order", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _scalar_count(connection: sa.Connection, sql: str) -> int:
    return connection.execute(sa.text(sql)).scalar_one()


def _validate_source_graph(connection: sa.Connection) -> None:
    invalid_owner_count = _scalar_count(
        connection,
        """
        SELECT count(*)
        FROM prescription AS p
        JOIN medical_document AS d ON d.id = p.document_id
        JOIN profile AS pf ON pf.id = p.profile_id
        JOIN ocr_job AS oj ON oj.id = p.source_ocr_job_id
        WHERE d.profile_id <> p.profile_id
           OR d.uploaded_by <> pf.user_id
           OR oj.document_id <> p.document_id
        """,
    )
    if invalid_owner_count:
        raise RuntimeError(
            "Prescription Version backfill refused: "
            f"{invalid_owner_count} prescription ownership or OCR provenance chains are invalid."
        )

    empty_prescription_count = _scalar_count(
        connection,
        """
        SELECT count(*)
        FROM prescription AS p
        WHERE NOT EXISTS (
            SELECT 1 FROM medication AS m WHERE m.prescription_id = p.id
        )
        """,
    )
    if empty_prescription_count:
        raise RuntimeError(
            f"Prescription Version backfill refused: {empty_prescription_count} prescriptions have no medications."
        )

    blank_medication_name_count = _scalar_count(
        connection,
        """
        SELECT count(*)
        FROM medication AS m
        WHERE length(trim(m.medication_name)) = 0
        """,
    )
    if blank_medication_name_count:
        raise RuntimeError(
            "Prescription Version backfill refused: "
            f"{blank_medication_name_count} medications have blank medication names."
        )

    partial_version_count = _scalar_count(
        connection,
        """
        SELECT count(*)
        FROM prescription AS p
        WHERE (p.active_version_id IS NULL) <> NOT EXISTS (
            SELECT 1 FROM prescription_version AS pv WHERE pv.prescription_id = p.id
        )
        """,
    )
    if partial_version_count:
        raise RuntimeError(
            "Prescription Version backfill refused: "
            f"{partial_version_count} prescriptions have a partial Version graph."
        )


def _backfill_batch(connection: sa.Connection) -> int:
    prescriptions = (
        connection.execute(
            sa.text(
                """
            SELECT p.id, p.prescribed_date, p.confirmed_at, p.created_at
            FROM prescription AS p
            WHERE p.active_version_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM prescription_version AS pv
                  WHERE pv.prescription_id = p.id
              )
            ORDER BY p.id
            LIMIT :batch_size
            FOR UPDATE OF p
            """
            ),
            {"batch_size": _BACKFILL_BATCH_SIZE},
        )
        .mappings()
        .all()
    )
    if not prescriptions:
        return 0

    version_ids = {row["id"]: str(uuid4()) for row in prescriptions}
    connection.execute(
        sa.insert(_prescription_version),
        [
            {
                "id": version_ids[row["id"]],
                "prescription_id": row["id"],
                "version_number": 1,
                "prescribed_date": row["prescribed_date"],
                "confirmed_at": row["confirmed_at"],
                "created_at": row["created_at"],
            }
            for row in prescriptions
        ],
    )

    medications = (
        connection.execute(
            sa.text(
                """
            SELECT
                m.prescription_id,
                m.medication_name,
                m.strength_text,
                m.dose_value,
                m.dose_unit,
                m.frequency_per_day,
                m.timing_text,
                m.duration_days,
                m.display_order,
                m.created_at
            FROM medication AS m
            WHERE m.prescription_id = ANY(:prescription_ids)
            ORDER BY m.prescription_id, m.display_order
            """
            ),
            {"prescription_ids": list(version_ids)},
        )
        .mappings()
        .all()
    )
    connection.execute(
        sa.insert(_prescription_version_medication),
        [
            {
                "id": str(uuid4()),
                "prescription_version_id": version_ids[row["prescription_id"]],
                "medication_name": row["medication_name"],
                "strength_text": row["strength_text"],
                "dose_value": row["dose_value"],
                "dose_unit": row["dose_unit"],
                "frequency_per_day": row["frequency_per_day"],
                "timing_text": row["timing_text"],
                "duration_days": row["duration_days"],
                "display_order": row["display_order"],
                "created_at": row["created_at"],
            }
            for row in medications
        ],
    )
    connection.execute(
        sa.text(
            """
            UPDATE prescription
            SET active_version_id = :version_id
            WHERE id = :prescription_id
              AND active_version_id IS NULL
            """
        ),
        [
            {"prescription_id": prescription_id, "version_id": version_id}
            for prescription_id, version_id in version_ids.items()
        ],
    )
    return len(prescriptions)


def _verify_backfill(connection: sa.Connection) -> None:
    missing_count = _scalar_count(
        connection,
        """
        SELECT count(*)
        FROM prescription AS p
        WHERE p.active_version_id IS NULL
           OR NOT EXISTS (
               SELECT 1
               FROM prescription_version AS pv
               WHERE pv.id = p.active_version_id
                 AND pv.prescription_id = p.id
           )
        """,
    )
    if missing_count:
        raise RuntimeError(
            f"Prescription Version backfill failed: {missing_count} prescriptions have no valid active Version."
        )

    mismatched_prescription_count = _scalar_count(
        connection,
        """
        SELECT count(*)
        FROM prescription AS p
        JOIN prescription_version AS pv ON pv.id = p.active_version_id
        WHERE pv.prescription_id <> p.id
           OR pv.prescribed_date IS DISTINCT FROM p.prescribed_date
           OR pv.confirmed_at IS DISTINCT FROM p.confirmed_at
        """,
    )
    if mismatched_prescription_count:
        raise RuntimeError(
            "Prescription Version backfill failed: "
            f"{mismatched_prescription_count} active Version headers differ from legacy prescriptions."
        )

    mismatched_medication_count = _scalar_count(
        connection,
        """
        SELECT count(*)
        FROM prescription AS p
        WHERE EXISTS (
            (
                SELECT
                    m.medication_name,
                    m.strength_text,
                    m.dose_value,
                    m.dose_unit,
                    m.frequency_per_day,
                    m.timing_text,
                    m.duration_days,
                    m.display_order
                FROM medication AS m
                WHERE m.prescription_id = p.id
                EXCEPT
                SELECT
                    pvm.medication_name,
                    pvm.strength_text,
                    pvm.dose_value,
                    pvm.dose_unit,
                    pvm.frequency_per_day,
                    pvm.timing_text,
                    pvm.duration_days,
                    pvm.display_order
                FROM prescription_version_medication AS pvm
                WHERE pvm.prescription_version_id = p.active_version_id
            )
            UNION ALL
            (
                SELECT
                    pvm.medication_name,
                    pvm.strength_text,
                    pvm.dose_value,
                    pvm.dose_unit,
                    pvm.frequency_per_day,
                    pvm.timing_text,
                    pvm.duration_days,
                    pvm.display_order
                FROM prescription_version_medication AS pvm
                WHERE pvm.prescription_version_id = p.active_version_id
                EXCEPT
                SELECT
                    m.medication_name,
                    m.strength_text,
                    m.dose_value,
                    m.dose_unit,
                    m.frequency_per_day,
                    m.timing_text,
                    m.duration_days,
                    m.display_order
                FROM medication AS m
                WHERE m.prescription_id = p.id
            )
        )
        """,
    )
    if mismatched_medication_count:
        raise RuntimeError(
            "Prescription Version backfill failed: "
            f"{mismatched_medication_count} active medication snapshots differ from legacy medications."
        )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE prescription IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE medication IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE prescription_version IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE prescription_version_medication IN SHARE ROW EXCLUSIVE MODE"))

    _validate_source_graph(connection)
    while _backfill_batch(connection):
        pass
    _verify_backfill(connection)


def downgrade() -> None:
    # Version snapshots are immutable audit data. Application rollback keeps the
    # backfill and returns to the legacy read path; re-upgrade verifies and reuses
    # the existing graph instead of deleting or recreating it.
    pass
