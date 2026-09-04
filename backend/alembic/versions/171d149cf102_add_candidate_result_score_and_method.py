"""add candidate result score and method

Revision ID: 171d149cf102
Revises: 164e30bc997f
Create Date: 2026-09-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "171d149cf102"
down_revision: str | Sequence[str] | None = "164e30bc997f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # result_rank와 같은 성격의 값이다 — Resolver가 후보를 찾는 시점에 이미 계산되어 있으므로
    # NOT NULL이다. 이 테이블은 아직 어떤 API도 실제로 값을 쓰지 않아(#171 라우터는 #261에서
    # fail-closed 503 스텁으로 남아 있음) 기존 행과의 충돌 위험이 없다.
    op.add_column(
        "medication_candidate_search_result",
        sa.Column("result_score", sa.Float(), nullable=False),
    )
    # 허용값 목록은 RAG-08(Resolver, #170)이 아직 확정하지 않아 CHECK 제약을 걸지 않는다 —
    # 미확정 공유 계약을 추정해 구현하지 않는다(#171 진행 기준).
    op.add_column(
        "medication_candidate_search_result",
        sa.Column("result_method", sa.String(length=50), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("medication_candidate_search_result", "result_method")
    op.drop_column("medication_candidate_search_result", "result_score")
