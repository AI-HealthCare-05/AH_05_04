"""Record whether OCR LLM structuring ran or was safely skipped.

Revision ID: 458c1d2e3f4a
Revises: 469a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

revision = "458c1d2e3f4a"
down_revision = "469a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ocr_job", sa.Column("llm_processing", sa.String(32), nullable=True))
    op.create_check_constraint(
        "chk_ocr_llm_processing",
        "ocr_job",
        "llm_processing IS NULL OR llm_processing IN ('APPLIED', 'SKIPPED_MINIMIZATION', 'NOT_REQUESTED')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM ocr_job WHERE llm_processing IS NOT NULL LIMIT 1")).first() is not None:
        raise RuntimeError("OCR LLM processing history must be handled before downgrade")
    op.drop_constraint("chk_ocr_llm_processing", "ocr_job", type_="check")
    op.drop_column("ocr_job", "llm_processing")
