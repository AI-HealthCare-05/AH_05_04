"""Record partial Snapshot loads as an exclusion receipt (#166 D-04).

수집은 성공했지만 구성원으로 승격하지 않은 행의 건수와 사유를 Snapshot 단위로 남긴다.
무결성 위반은 Snapshot 자체를 만들지 않으므로 이 표에 남지 않는다.
업무 판정은 Python Service/Repository에서 하며 Trigger·RLS·업무용 DB 함수를 추가하지 않는다.
"""

import sqlalchemy as sa
from alembic import op

revision = "166f40516273"
down_revision = "469a1b2c3d4e"
branch_labels = None
depends_on = None
TABLE = "rag_source_snapshot_exclusion"
REASONS = ("EMPTY_COMPONENT_FIELDS",)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(36), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("excluded_row_count", sa.Integer(), nullable=False),
        sa.Column("retained_row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_rag_source_snapshot_exclusion"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_source_snapshot_exclusion_snapshot",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("source_snapshot_id", "reason", name="uq_rag_source_snapshot_exclusion"),
        sa.CheckConstraint(
            f"reason IN ({', '.join(repr(value) for value in REASONS)})",
            name="chk_rag_source_snapshot_exclusion_reason",
        ),
        sa.CheckConstraint("excluded_row_count >= 0", name="chk_rag_source_snapshot_exclusion_excluded_count"),
        sa.CheckConstraint("retained_row_count >= 0", name="chk_rag_source_snapshot_exclusion_retained_count"),
        sa.CheckConstraint(
            "excluded_row_count + retained_row_count <= source_row_count",
            name="chk_rag_source_snapshot_exclusion_row_totals",
        ),
    )
    op.create_index("idx_rag_source_snapshot_exclusion_snapshot", TABLE, ["source_snapshot_id"])


def downgrade() -> None:
    # 기록된 제외 receipt가 있으면 partial 적재 근거가 사라지므로 되돌리지 않는다.
    if op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE})")).scalar():
        raise RuntimeError("Snapshot exclusion receipts exist; downgrade would drop partial-load evidence.")
    op.drop_index("idx_rag_source_snapshot_exclusion_snapshot", table_name=TABLE)
    op.drop_table(TABLE)
