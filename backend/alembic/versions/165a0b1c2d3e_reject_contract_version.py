"""Persist the per-run reject contract without rewriting legacy NULL history."""

import sqlalchemy as sa
from alembic import op

revision = "165a0b1c2d3e"
down_revision = "362c3d4e5f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rag_source_ingestion_run", sa.Column("reject_code_contract_version", sa.String(100), nullable=True))
    op.create_check_constraint(
        "chk_rag_run_reject_contract_nonblank",
        "rag_source_ingestion_run",
        "reject_code_contract_version IS NULL OR length(trim(reject_code_contract_version)) > 0",
    )
    # Existing #362 column-level lifecycle grants exclude this new provenance column.


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_ingestion_run IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM rag_source_ingestion_run WHERE reject_code_contract_version IS NOT NULL)")
    ):
        raise RuntimeError("Reject contract history must be preserved; use a forward fix.")
    op.drop_constraint("chk_rag_run_reject_contract_nonblank", "rag_source_ingestion_run", type_="check")
    op.drop_column("rag_source_ingestion_run", "reject_code_contract_version")
