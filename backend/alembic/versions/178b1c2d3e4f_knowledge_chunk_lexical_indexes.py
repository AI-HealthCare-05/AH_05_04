"""Add GIN expression index for FTS (simple) and GIN trgm index on knowledge_chunk.chunk_text.

Revision ID: 178b1c2d3e4f
Revises: 458c1d2e3f4a
"""

import sqlalchemy as sa
from alembic import op

revision = "178b1c2d3e4f"
down_revision = "458c1d2e3f4a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_fts_simple "
            "ON knowledge_chunk USING gin (to_tsvector('simple', chunk_text))"
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_chunk_text_trgm "
            "ON knowledge_chunk USING gin (chunk_text gin_trgm_ops)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_knowledge_chunk_chunk_text_trgm"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_knowledge_chunk_fts_simple"))
