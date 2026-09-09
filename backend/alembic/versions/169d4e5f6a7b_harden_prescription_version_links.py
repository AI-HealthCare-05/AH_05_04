"""harden prescription version links after read cutover

Revision ID: 169d4e5f6a7b
Revises: 164b6c7d8e9f
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "169d4e5f6a7b"
down_revision: str | Sequence[str] | None = "164b6c7d8e9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _count(connection: sa.Connection, sql: str) -> int:
    return connection.execute(sa.text(sql)).scalar_one()


def upgrade() -> None:
    connection = op.get_bind()
    for table in ("prescription", "guide", "chat_session", "ai_job"):
        connection.execute(sa.text(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE"))

    missing = {
        "prescription.active_version_id": _count(
            connection,
            "SELECT count(*) FROM prescription WHERE active_version_id IS NULL",
        ),
        "guide.prescription_version_id": _count(
            connection,
            "SELECT count(*) FROM guide WHERE prescription_version_id IS NULL",
        ),
        "chat_session.prescription_version_id": _count(
            connection,
            "SELECT count(*) FROM chat_session WHERE prescription_version_id IS NULL",
        ),
        "ai_job.GUIDE_OR_CHAT.prescription_version_id": _count(
            connection,
            "SELECT count(*) FROM ai_job WHERE job_type IN ('GUIDE', 'CHAT') AND prescription_version_id IS NULL",
        ),
        "ai_job.OCR.prescription_version_id": _count(
            connection,
            "SELECT count(*) FROM ai_job WHERE job_type = 'OCR' AND prescription_version_id IS NOT NULL",
        ),
    }
    invalid = {column: count for column, count in missing.items() if count}
    if invalid:
        summary = ", ".join(f"{column}={count}" for column, count in invalid.items())
        raise RuntimeError(f"Prescription Version hardening refused: invalid provenance remains ({summary}).")

    op.alter_column("prescription", "active_version_id", existing_type=sa.CHAR(length=36), nullable=False)
    op.alter_column("guide", "prescription_version_id", existing_type=sa.CHAR(length=36), nullable=False)
    op.alter_column("chat_session", "prescription_version_id", existing_type=sa.CHAR(length=36), nullable=False)
    op.create_check_constraint(
        "chk_ai_job_prescription_version_by_type",
        "ai_job",
        "(job_type = 'OCR' AND prescription_version_id IS NULL) OR "
        "(job_type IN ('GUIDE', 'CHAT') AND prescription_version_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("chk_ai_job_prescription_version_by_type", "ai_job", type_="check")
    op.alter_column("chat_session", "prescription_version_id", existing_type=sa.CHAR(length=36), nullable=True)
    op.alter_column("guide", "prescription_version_id", existing_type=sa.CHAR(length=36), nullable=True)
    op.alter_column("prescription", "active_version_id", existing_type=sa.CHAR(length=36), nullable=True)
