"""Seal Runtime transition revisions for the Python transition boundary.

Revision ID: 398f60718293
Revises: 398e5f607182
"""

import sqlalchemy as sa
from alembic import op

revision = "398f60718293"
down_revision = "398e5f607182"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_rag_runtime_transition_environment_revision"


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE public.rag_runtime_environment_transition IN ACCESS EXCLUSIVE MODE"))
    duplicates = connection.scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT environment_id,environment_revision "
            "FROM public.rag_runtime_environment_transition GROUP BY environment_id,environment_revision "
            "HAVING count(*)>1) duplicate_revisions"
        )
    )
    if duplicates:
        raise RuntimeError("Runtime transition revision sealing refused: duplicate revisions exist")
    op.create_unique_constraint(
        _CONSTRAINT,
        "rag_runtime_environment_transition",
        ["environment_id", "environment_revision"],
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "rag_runtime_environment_transition", type_="unique")
