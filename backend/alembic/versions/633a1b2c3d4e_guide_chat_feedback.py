"""Guide and Chat feedback with bounded retention (#633)."""

import sqlalchemy as sa
from alembic import op

revision = "633a1b2c3d4e"
down_revision = "206b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, target, parent in (
        ("guide_feedback", "guide_id", "guide"),
        ("chat_message_feedback", "chat_message_id", "chat_message"),
    ):
        op.create_table(
            table,
            sa.Column("id", sa.CHAR(36), primary_key=True),
            sa.Column(target, sa.CHAR(36), sa.ForeignKey(f"{parent}.id", ondelete="CASCADE"), nullable=False),
            sa.Column("rating", sa.String(8), nullable=False),
            sa.Column("comment", sa.String(1000), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(target, name=f"uq_{table}_target"),
            sa.CheckConstraint("rating IN ('POSITIVE', 'NEGATIVE')", name=f"chk_{table}_rating"),
        )
        op.create_index(f"idx_{table}_created", table, ["created_at"])
        op.create_index(f"idx_{table}_review", table, ["rating", "updated_at", "id"])


def downgrade() -> None:
    op.execute("LOCK TABLE guide_feedback, chat_message_feedback IN ACCESS EXCLUSIVE MODE")
    for table in ("guide_feedback", "chat_message_feedback"):
        if op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError("Feedback exists; explicitly remove retained feedback before downgrade.")
    op.drop_table("chat_message_feedback")
    op.drop_table("guide_feedback")
