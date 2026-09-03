"""add outbox_event stream envelope fields

Revision ID: 8d4f1a6c9e2b
Revises: 146a1b2c3d4e
Create Date: 2026-09-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "8d4f1a6c9e2b"
down_revision: str | Sequence[str] | None = "146a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOMAIN_TYPE_VALUES = ("OCR_JOB", "GUIDE", "CHAT_MESSAGE")


def _ensure_downgrade_is_data_safe(connection: Connection) -> None:
    """접수 시점에 채운 trace_id·domain_type·domain_id가 있는 뒤의 downgrade가 그 값을
    조용히 지우지 않도록 막습니다."""

    outbox_event = sa.table(
        "outbox_event",
        sa.column("trace_id"),
        sa.column("domain_type"),
        sa.column("domain_id"),
    )

    linked_count = connection.execute(
        sa.select(sa.func.count())
        .select_from(outbox_event)
        .where(
            sa.or_(
                outbox_event.c.trace_id.is_not(None),
                outbox_event.c.domain_type.is_not(None),
                outbox_event.c.domain_id.is_not(None),
            )
        )
    ).scalar_one()

    if linked_count:
        raise RuntimeError(
            "Cannot downgrade revision 8d4f1a6c9e2b while outbox_event trace_id/domain_type/domain_id "
            "data exists. Production must use a forward-fix. In a non-production environment, back up "
            "and remove or migrate the affected data through an approved rollback procedure first."
        )


def upgrade() -> None:
    op.add_column("outbox_event", sa.Column("trace_id", sa.String(length=100), nullable=True))
    op.add_column(
        "outbox_event",
        sa.Column(
            "domain_type",
            sa.Enum(*_DOMAIN_TYPE_VALUES, name="domaintype", native_enum=False, length=20),
            nullable=True,
        ),
    )
    op.add_column("outbox_event", sa.Column("domain_id", sa.CHAR(length=36), nullable=True))
    domain_type_list_sql = ", ".join(f"'{value}'" for value in _DOMAIN_TYPE_VALUES)
    op.create_check_constraint(
        "chk_outbox_domain_type",
        "outbox_event",
        f"domain_type IS NULL OR domain_type IN ({domain_type_list_sql})",
    )


def downgrade() -> None:
    connection = op.get_bind()
    _ensure_downgrade_is_data_safe(connection)

    op.drop_constraint("chk_outbox_domain_type", "outbox_event", type_="check")
    op.drop_column("outbox_event", "domain_id")
    op.drop_column("outbox_event", "domain_type")
    op.drop_column("outbox_event", "trace_id")
