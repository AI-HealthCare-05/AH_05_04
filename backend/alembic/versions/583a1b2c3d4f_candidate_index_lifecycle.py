"""Add Candidate Index READY lifecycle guard (#583)."""

import sqlalchemy as sa
from alembic import op

revision = "583a1b2c3d4f"
down_revision = "178c2d3e4f50"
branch_labels = None
depends_on = None

VERSION_TABLE = "rag_candidate_index_version"
MEMBER_TABLE = "rag_candidate_index_member"
READY_INDEX_NAME = "uq_rag_candidate_index_ready_per_code"
EMBEDDING_STORAGE_HASH_CHECK = "chk_rag_candidate_index_member_embedding_storage_hash"


def _candidate_index_records_exist() -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {VERSION_TABLE})")).scalar())


def upgrade() -> None:
    op.add_column(MEMBER_TABLE, sa.Column("embedding_storage_hash", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        EMBEDDING_STORAGE_HASH_CHECK,
        MEMBER_TABLE,
        "(embedding IS NULL AND embedding_storage_hash IS NULL) OR "
        "(embedding IS NOT NULL AND embedding_storage_hash ~ '^[0-9a-f]{64}$')",
    )
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
    op.drop_constraint(EMBEDDING_STORAGE_HASH_CHECK, MEMBER_TABLE, type_="check")
    op.drop_column(MEMBER_TABLE, "embedding_storage_hash")
