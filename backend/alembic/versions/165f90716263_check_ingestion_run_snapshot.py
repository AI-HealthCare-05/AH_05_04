"""Enforce failed/no-change ingestion Snapshot references.

Revision ID: 165f90716263
Revises: 165e8f706152
"""

from alembic import op

revision = "165f90716263"
down_revision = "165e8f706152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "chk_rag_ingestion_run_snapshot_status",
        "rag_source_ingestion_run",
        "(run_status <> 'FAILED' OR snapshot_id IS NULL) AND (run_status <> 'NO_CHANGE' OR snapshot_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("chk_rag_ingestion_run_snapshot_status", "rag_source_ingestion_run", type_="check")
