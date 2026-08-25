"""add ocr job created sequence

Revision ID: f4c2a1b7d9e3
Revises: b194e393dba7
Create Date: 2026-08-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4c2a1b7d9e3"
down_revision: str | Sequence[str] | None = "b194e393dba7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATED_SEQUENCE_TYPE = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")


def upgrade() -> None:
    op.add_column(
        "ocr_job",
        sa.Column(
            "created_sequence",
            _CREATED_SEQUENCE_TYPE,
            nullable=True,
        ),
    )

    # MySQL 전용 백필: 세션 변수와 UPDATE...ORDER BY로 기존 행에 순번을 부여합니다.
    # PostgreSQL 전환 시 이 백필과 아래 UUID_SHORT() 기본값을 함께 다시 설계해야 합니다.
    op.execute("SET @ocr_job_created_sequence := 0")
    op.execute(
        """
        UPDATE ocr_job
        SET created_sequence = (@ocr_job_created_sequence := @ocr_job_created_sequence + 1)
        ORDER BY created_at ASC, id ASC
        """
    )

    op.alter_column(
        "ocr_job",
        "created_sequence",
        existing_type=_CREATED_SEQUENCE_TYPE,
        nullable=False,
        server_default=sa.text("(UUID_SHORT())"),
    )

    # 기존 idx_ocr_document_created(document_id, created_at, id)는 document_id FK를
    # 지원할 수 있으므로 삭제하지 않습니다. 최신 OCR 작업 정렬에는 별도 인덱스를 추가합니다.
    op.create_index(
        "idx_ocr_document_created_seq",
        "ocr_job",
        ["document_id", "created_at", "created_sequence"],
    )


def downgrade() -> None:
    op.drop_index("idx_ocr_document_created_seq", table_name="ocr_job")
    op.drop_column("ocr_job", "created_sequence")
