"""add candidate search medication snapshot

Revision ID: 171798152fa3
Revises: 171d149cf102
Create Date: 2026-09-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "171798152fa3"
down_revision: str | Sequence[str] | None = "171d149cf102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # prescription_version_medication_id는 #169(Prescription Version)가 만들 안정적인
    # FK 자리를 미리 잡아둔 placeholder라 실제 FK가 없다. medication.medication_name/
    # strength_text는 사용자가 나중에 정정하면 값이 바뀌므로, Search가 실제로 어떤 값을
    # 대상으로 실행됐는지는 여기 스냅샷 컬럼에 검색 생성 시점 값을 복사해 보존한다
    # (#260 Product Identity 원칙과 같은 이유 — 살아있는 FK 참조만으로는 과거 시점 값을
    # 재구성할 수 없다).
    op.add_column(
        "medication_candidate_search",
        sa.Column("medication_name_snapshot", sa.String(length=255), nullable=False),
    )
    op.add_column(
        "medication_candidate_search",
        sa.Column("strength_text_snapshot", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("medication_candidate_search", "strength_text_snapshot")
    op.drop_column("medication_candidate_search", "medication_name_snapshot")
