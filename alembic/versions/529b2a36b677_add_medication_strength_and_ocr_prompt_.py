"""add medication strength and ocr prompt version

Revision ID: 529b2a36b677
Revises: a9b8c7d6e5f4
Create Date: 2026-08-26 15:03:09.215134

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "529b2a36b677"
down_revision: str | Sequence[str] | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 확정 처방 약제에 제품 함량을 별도로 저장합니다.
    op.add_column(
        "medication",
        sa.Column(
            "strength_text",
            sa.String(length=100),
            nullable=True,
        ),
    )

    # OCR 구조화에 사용한 prompt 버전을 기록합니다.
    op.add_column(
        "ocr_job",
        sa.Column(
            "prompt_version",
            sa.String(length=100),
            nullable=True,
        ),
    )

    # MEDICATION_STRENGTH를 허용하도록 기존 check constraint를 교체합니다.
    op.drop_constraint(
        "chk_field_type",
        "extracted_field",
        type_="check",
    )
    op.create_check_constraint(
        "chk_field_type",
        "extracted_field",
        "field_type IN ("
        "'MEDICATION_NAME', 'MEDICATION_STRENGTH', "
        "'DOSE_VALUE', 'DOSE_UNIT', 'FREQUENCY_PER_DAY', "
        "'TIMING', 'PRESCRIBED_DATE', 'DURATION_DAYS'"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "chk_field_type",
        "extracted_field",
        type_="check",
    )
    op.create_check_constraint(
        "chk_field_type",
        "extracted_field",
        "field_type IN ("
        "'MEDICATION_NAME', 'DOSE_VALUE', 'DOSE_UNIT', "
        "'FREQUENCY_PER_DAY', 'TIMING', "
        "'PRESCRIBED_DATE', 'DURATION_DAYS'"
        ")",
    )

    op.drop_column("ocr_job", "prompt_version")
    op.drop_column("medication", "strength_text")
