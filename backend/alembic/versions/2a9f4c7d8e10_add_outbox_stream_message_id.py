"""add outbox stream message id

Revision ID: 2a9f4c7d8e10
Revises: 20fd11d29ecc
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a9f4c7d8e10"
down_revision: str | Sequence[str] | None = "20fd11d29ecc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_event",
        sa.Column("stream_message_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    outbox_event = sa.table(
        "outbox_event",
        sa.column("stream_message_id", sa.String(length=100)),
    )
    published_ids = op.get_bind().execute(
        sa.select(sa.func.count()).select_from(outbox_event).where(outbox_event.c.stream_message_id.is_not(None))
    )
    if published_ids.scalar_one() > 0:
        raise RuntimeError(
            "Cannot downgrade revision 2a9f4c7d8e10 while outbox_event stream_message_id values exist. "
            "Production must use a forward-fix."
        )

    op.drop_column("outbox_event", "stream_message_id")
