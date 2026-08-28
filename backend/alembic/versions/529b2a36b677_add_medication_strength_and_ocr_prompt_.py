"""add medication strength and ocr prompt version

Revision ID: 529b2a36b677
Revises: a9b8c7d6e5f4
Create Date: 2026-08-26 15:03:09.215134

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "529b2a36b677"
down_revision: str | Sequence[str] | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_downgrade_is_data_safe(
    connection: Connection,
) -> None:
    """새 필드의 데이터를 잃는 downgrade를 DDL 실행 전에 차단합니다."""

    extracted_field = sa.table(
        "extracted_field",
        sa.column("field_type"),
    )
    medication = sa.table(
        "medication",
        sa.column("strength_text"),
    )
    ocr_job = sa.table(
        "ocr_job",
        sa.column("prompt_version"),
    )

    medication_strength_count = connection.execute(
        sa.select(sa.func.count())
        .select_from(extracted_field)
        .where(extracted_field.c.field_type == "MEDICATION_STRENGTH")
    ).scalar_one()

    stored_strength_count = connection.execute(
        sa.select(sa.func.count()).select_from(medication).where(medication.c.strength_text.is_not(None))
    ).scalar_one()

    prompt_version_count = connection.execute(
        sa.select(sa.func.count()).select_from(ocr_job).where(ocr_job.c.prompt_version.is_not(None))
    ).scalar_one()

    if medication_strength_count or stored_strength_count or prompt_version_count:
        raise RuntimeError(
            "Cannot downgrade revision 529b2a36b677 while "
            "MEDICATION_STRENGTH, medication.strength_text, or "
            "ocr_job.prompt_version data exists. "
            "Production must use a forward-fix. In a non-production "
            "environment, back up and remove or migrate the affected "
            "data through an approved rollback procedure first."
        )


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
    # Production에서는 이 revision을 downgrade하지 않고 forward-fix합니다.
    # 비운영 환경에서도 새 필드 데이터가 존재하면 데이터 손실과
    # check constraint 생성 실패를 방지하기 위해 DDL 전에 중단합니다.
    connection = op.get_bind()
    _ensure_downgrade_is_data_safe(connection)

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
