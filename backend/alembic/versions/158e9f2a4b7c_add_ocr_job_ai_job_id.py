"""add ocr_job ai_job_id

Revision ID: 158e9f2a4b7c
Revises: 146a1b2c3d4e
Create Date: 2026-09-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "158e9f2a4b7c"
down_revision: str | Sequence[str] | None = "146a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ocr_job", sa.Column("ai_job_id", sa.CHAR(length=36), nullable=True))
    op.create_foreign_key(
        "fk_ocr_job_ai_job",
        "ocr_job",
        "ai_job",
        ["ai_job_id"],
        ["id"],
    )
    op.create_index("idx_ocr_job_ai_job", "ocr_job", ["ai_job_id"])


def downgrade() -> None:
    op.drop_index("idx_ocr_job_ai_job", table_name="ocr_job")
    op.drop_constraint("fk_ocr_job_ai_job", "ocr_job", type_="foreignkey")
    op.drop_column("ocr_job", "ai_job_id")
