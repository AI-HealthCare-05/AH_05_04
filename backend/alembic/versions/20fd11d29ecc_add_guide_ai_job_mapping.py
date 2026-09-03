"""add Guide to AI Job mapping

Revision ID: 20fd11d29ecc
Revises: c3f8a12d9e47
Create Date: 2026-09-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "20fd11d29ecc"
down_revision: str | Sequence[str] | None = "c3f8a12d9e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_downgrade_is_data_safe(connection: Connection) -> None:
    """Guide 쓰기를 차단한 뒤 연결 정보가 존재하는 downgrade를 거부합니다."""

    connection.execute(sa.text("LOCK TABLE guide IN ACCESS EXCLUSIVE MODE"))

    guide = sa.table(
        "guide",
        sa.column("ai_job_id"),
    )

    linked_guide_count = connection.execute(
        sa.select(sa.func.count()).select_from(guide).where(guide.c.ai_job_id.is_not(None))
    ).scalar_one()

    if linked_guide_count > 0:
        raise RuntimeError(
            "Cannot downgrade while guide.ai_job_id contains linked AI Jobs. "
            "Remove the links explicitly before retrying the downgrade."
        )


def upgrade() -> None:
    # 기존 Guide 행은 synthetic AI Job을 생성하거나 backfill하지 않습니다.
    # nullable이며 server default가 없으므로 기존 행은 모두 NULL로 유지됩니다.
    op.add_column(
        "guide",
        sa.Column(
            "ai_job_id",
            sa.CHAR(length=36),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_guide_ai_job",
        "guide",
        "ai_job",
        ["ai_job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 하나의 AI Job이 둘 이상의 Guide 행에 연결되는 것을 차단합니다.
    # PostgreSQL unique 제약은 NULL을 여러 건 허용하므로 기존 Guide 행에는
    # 영향을 주지 않습니다.
    op.create_unique_constraint(
        "uq_guide_ai_job",
        "guide",
        ["ai_job_id"],
    )


def downgrade() -> None:
    # 연결 데이터가 있으면 컬럼 제거 전에 중단하여 mapping 유실을 방지합니다.
    connection = op.get_bind()
    _ensure_downgrade_is_data_safe(connection)

    op.drop_constraint(
        "uq_guide_ai_job",
        "guide",
        type_="unique",
    )
    op.drop_constraint(
        "fk_guide_ai_job",
        "guide",
        type_="foreignkey",
    )
    op.drop_column("guide", "ai_job_id")
