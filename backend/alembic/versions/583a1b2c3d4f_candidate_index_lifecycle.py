"""Add Candidate Index READY lifecycle guard (#583)."""

import sqlalchemy as sa
from alembic import op

revision = "583a1b2c3d4f"
down_revision = "556a1b2c3d4e"
branch_labels = None
depends_on = None

VERSION_TABLE = "rag_candidate_index_version"
READY_INDEX_NAME = "uq_rag_candidate_index_ready_per_code"


def _candidate_index_records_exist() -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {VERSION_TABLE})")).scalar())


def upgrade() -> None:
    op.create_index(
        READY_INDEX_NAME,
        VERSION_TABLE,
        ["index_code"],
        unique=True,
        postgresql_where=sa.text("status = 'READY'"),
    )


def downgrade() -> None:
    if _candidate_index_records_exist():
        raise RuntimeError("Candidate Index lifecycle records exist; downgrade would remove READY lifecycle guard.")
    op.drop_index(READY_INDEX_NAME, table_name=VERSION_TABLE)
