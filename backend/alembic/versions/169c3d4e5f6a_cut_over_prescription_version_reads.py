"""cut over prescription version reads and dependent references

Revision ID: 169c3d4e5f6a
Revises: 169b2c3d4e5f
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "169c3d4e5f6a"
down_revision: str | Sequence[str] | None = "169b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _count(connection: sa.Connection, sql: str) -> int:
    return connection.execute(sa.text(sql)).scalar_one()


def _defensive_backfill(connection: sa.Connection) -> None:
    partial = _count(
        connection,
        """
        SELECT count(*) FROM prescription p
        WHERE (p.active_version_id IS NULL) <> NOT EXISTS (
            SELECT 1 FROM prescription_version pv WHERE pv.prescription_id = p.id
        )
        """,
    )
    if partial:
        raise RuntimeError(f"Read cutover refused: {partial} prescriptions have a partial Version graph.")

    invalid = _count(
        connection,
        """
        SELECT count(*) FROM prescription p
        JOIN medical_document d ON d.id = p.document_id
        JOIN profile pf ON pf.id = p.profile_id
        JOIN ocr_job oj ON oj.id = p.source_ocr_job_id
        WHERE d.profile_id <> p.profile_id
           OR d.uploaded_by <> pf.user_id
           OR oj.document_id <> p.document_id
           OR NOT EXISTS (SELECT 1 FROM medication m WHERE m.prescription_id = p.id)
           OR EXISTS (
               SELECT 1 FROM medication m
               WHERE m.prescription_id = p.id AND length(trim(m.medication_name)) = 0
           )
        """,
    )
    if invalid:
        raise RuntimeError(f"Read cutover refused: {invalid} legacy prescription graphs are invalid.")

    connection.execute(
        sa.text(
            """
            INSERT INTO prescription_version
                (id, prescription_id, version_number, prescribed_date, confirmed_at, created_at)
            SELECT gen_random_uuid()::text, p.id, 1, p.prescribed_date, p.confirmed_at, p.created_at
            FROM prescription p
            WHERE p.active_version_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM prescription_version pv WHERE pv.prescription_id = p.id)
            ORDER BY p.id
            """
        )
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO prescription_version_medication
                (id, prescription_version_id, medication_name, strength_text, dose_value, dose_unit,
                 frequency_per_day, timing_text, duration_days, display_order, created_at)
            SELECT gen_random_uuid()::text, pv.id, m.medication_name, m.strength_text, m.dose_value,
                   m.dose_unit, m.frequency_per_day, m.timing_text, m.duration_days,
                   m.display_order, m.created_at
            FROM prescription p
            JOIN prescription_version pv
              ON pv.prescription_id = p.id AND pv.version_number = 1
            JOIN medication m ON m.prescription_id = p.id
            WHERE p.active_version_id IS NULL
            ORDER BY p.id, m.display_order
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE prescription p SET active_version_id = pv.id
            FROM prescription_version pv
            WHERE p.active_version_id IS NULL
              AND pv.prescription_id = p.id AND pv.version_number = 1
            """
        )
    )

    invalid_active = _count(
        connection,
        """
        SELECT count(*) FROM prescription p
        WHERE p.active_version_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM prescription_version pv
            WHERE pv.id = p.active_version_id AND pv.prescription_id = p.id
              AND EXISTS (
                  SELECT 1 FROM prescription_version_medication pvm
                  WHERE pvm.prescription_version_id = pv.id
              )
        )
        """,
    )
    if invalid_active:
        raise RuntimeError(f"Read cutover failed: {invalid_active} prescriptions have no complete active Version.")


_MAPPING_SQL = """
SELECT m.id AS legacy_id, pvm.id AS version_medication_id
FROM medication m
JOIN prescription p ON p.id = m.prescription_id
JOIN prescription_version pv ON pv.id = p.active_version_id AND pv.version_number = 1
JOIN prescription_version_medication pvm
  ON pvm.prescription_version_id = pv.id AND pvm.display_order = m.display_order
WHERE pvm.medication_name = m.medication_name
  AND pvm.strength_text IS NOT DISTINCT FROM m.strength_text
  AND pvm.dose_value IS NOT DISTINCT FROM m.dose_value
  AND pvm.dose_unit IS NOT DISTINCT FROM m.dose_unit
  AND pvm.frequency_per_day IS NOT DISTINCT FROM m.frequency_per_day
  AND pvm.timing_text IS NOT DISTINCT FROM m.timing_text
  AND pvm.duration_days IS NOT DISTINCT FROM m.duration_days
"""


def _remap_placeholder_ids(connection: sa.Connection, table: str) -> None:
    unresolved = _count(
        connection,
        f"""
        WITH mapping AS ({_MAPPING_SQL})
        SELECT count(*) FROM {table} target
        WHERE NOT EXISTS (
            SELECT 1 FROM mapping m WHERE m.legacy_id = target.prescription_version_medication_id
        ) AND NOT EXISTS (
            SELECT 1 FROM prescription_version_medication pvm
            WHERE pvm.id = target.prescription_version_medication_id
        )
        """,
    )
    if unresolved:
        raise RuntimeError(f"Read cutover refused: {unresolved} {table} rows cannot be mapped to Version 1.")
    connection.execute(
        sa.text(
            f"""
            WITH mapping AS ({_MAPPING_SQL})
            UPDATE {table} target
            SET prescription_version_medication_id = mapping.version_medication_id
            FROM mapping
            WHERE target.prescription_version_medication_id = mapping.legacy_id
            """
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    for table in (
        "prescription",
        "medication",
        "prescription_version",
        "prescription_version_medication",
        "medication_candidate_search",
        "medication_identification",
        "guide",
        "chat_session",
        "ai_job",
    ):
        connection.execute(sa.text(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE"))

    _defensive_backfill(connection)
    _remap_placeholder_ids(connection, "medication_candidate_search")
    _remap_placeholder_ids(connection, "medication_identification")
    inconsistent_identifications = _count(
        connection,
        """
        SELECT count(*) FROM medication_identification mi
        JOIN medication_candidate_search mcs ON mcs.id = mi.candidate_search_id
        WHERE mi.prescription_version_medication_id <> mcs.prescription_version_medication_id
        """,
    )
    if inconsistent_identifications:
        raise RuntimeError(
            "Read cutover refused: "
            f"{inconsistent_identifications} Identification rows disagree with their Candidate Search."
        )

    op.drop_constraint("fk_medication_identification_result", "medication_identification", type_="foreignkey")
    op.drop_constraint("fk_medication_identification_search", "medication_identification", type_="foreignkey")
    op.drop_constraint(
        "fk_medication_candidate_result_search", "medication_candidate_search_result", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_medication_candidate_result_search",
        "medication_candidate_search_result",
        "medication_candidate_search",
        ["search_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_medication_identification_search",
        "medication_identification",
        "medication_candidate_search",
        ["candidate_search_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_medication_identification_result",
        "medication_identification",
        "medication_candidate_search_result",
        ["candidate_search_result_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("guide", sa.Column("prescription_version_id", sa.CHAR(length=36), nullable=True))
    op.add_column("chat_session", sa.Column("prescription_version_id", sa.CHAR(length=36), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE guide g SET prescription_version_id = p.active_version_id FROM prescription p WHERE p.id = g.prescription_id"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE chat_session c SET prescription_version_id = p.active_version_id FROM prescription p WHERE p.id = c.prescription_id"
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE ai_job j SET prescription_version_id = g.prescription_version_id
            FROM guide g
            WHERE g.ai_job_id = j.id AND j.prescription_version_id IS NULL
            """
        )
    )
    op.create_foreign_key(
        "fk_medication_candidate_search_version_medication",
        "medication_candidate_search",
        "prescription_version_medication",
        ["prescription_version_medication_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_medication_identification_version_medication",
        "medication_identification",
        "prescription_version_medication",
        ["prescription_version_medication_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_guide_prescription_version_prescription",
        "guide",
        "prescription_version",
        ["prescription_version_id", "prescription_id"],
        ["id", "prescription_id"],
    )
    op.create_foreign_key(
        "fk_chat_session_prescription_version_prescription",
        "chat_session",
        "prescription_version",
        ["prescription_version_id", "prescription_id"],
        ["id", "prescription_id"],
    )
    op.create_foreign_key(
        "fk_ai_job_prescription_version",
        "ai_job",
        "prescription_version",
        ["prescription_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_ai_job_prescription_version", "ai_job", ["prescription_version_id"])


def downgrade() -> None:
    connection = op.get_bind()
    referenced = _count(
        connection,
        "SELECT (SELECT count(*) FROM medication_candidate_search) + (SELECT count(*) FROM medication_identification)",
    )
    if referenced:
        raise RuntimeError("Read cutover downgrade refused: remapped Candidate/Identification IDs are irreversible.")
    op.drop_index("idx_ai_job_prescription_version", table_name="ai_job")
    op.drop_constraint("fk_ai_job_prescription_version", "ai_job", type_="foreignkey")
    op.drop_constraint("fk_chat_session_prescription_version_prescription", "chat_session", type_="foreignkey")
    op.drop_constraint("fk_guide_prescription_version_prescription", "guide", type_="foreignkey")
    op.drop_constraint(
        "fk_medication_identification_version_medication", "medication_identification", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_medication_candidate_search_version_medication", "medication_candidate_search", type_="foreignkey"
    )
    op.drop_constraint("fk_medication_identification_result", "medication_identification", type_="foreignkey")
    op.drop_constraint("fk_medication_identification_search", "medication_identification", type_="foreignkey")
    op.drop_constraint(
        "fk_medication_candidate_result_search", "medication_candidate_search_result", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_medication_candidate_result_search",
        "medication_candidate_search_result",
        "medication_candidate_search",
        ["search_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_medication_identification_search",
        "medication_identification",
        "medication_candidate_search",
        ["candidate_search_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_medication_identification_result",
        "medication_identification",
        "medication_candidate_search_result",
        ["candidate_search_result_id"],
        ["id"],
    )
    op.drop_column("chat_session", "prescription_version_id")
    op.drop_column("guide", "prescription_version_id")
