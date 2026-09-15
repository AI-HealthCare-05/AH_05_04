"""Add Candidate Index READY lifecycle guard (#583)."""

import sqlalchemy as sa
from alembic import op

revision = "583a1b2c3d4f"
down_revision = "168a1b2c3d4e"
branch_labels = None
depends_on = None

VERSION_TABLE = "rag_candidate_index_version"


def upgrade() -> None:
    op.create_index(
        "uq_rag_candidate_index_ready_per_code",
        VERSION_TABLE,
        ["index_code"],
        unique=True,
        postgresql_where=sa.text("status = 'READY'"),
    )


def downgrade() -> None:
    op.drop_index("uq_rag_candidate_index_ready_per_code", table_name=VERSION_TABLE)
