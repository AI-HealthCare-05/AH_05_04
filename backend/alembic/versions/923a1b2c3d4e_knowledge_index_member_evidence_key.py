"""Bind opaque evidence keys to Knowledge Evidence Index members.

Revision ID: 923a1b2c3d4e
Revises: 180a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

revision = "923a1b2c3d4e"
down_revision = "180a1b2c3d4e"
branch_labels = None
depends_on = None

TABLE = "rag_knowledge_index_member"


def upgrade() -> None:
    # Existing index members have no authoritative key.  Preserve them as NULL:
    # runtime readers reject those rows and only a source-authoritative rebuild can
    # supply bindings.  No legacy identifier is transformed into a key here.
    op.add_column(TABLE, sa.Column("evidence_key", sa.String(length=300), nullable=True))
    op.create_check_constraint(
        "chk_rag_knowledge_index_member_evidence_key_nonblank",
        TABLE,
        "evidence_key IS NULL OR length(trim(evidence_key)) > 0",
    )
    op.create_check_constraint(
        "chk_rag_knowledge_index_member_evidence_key_nfc",
        TABLE,
        "evidence_key IS NULL OR evidence_key = normalize(evidence_key, NFC)",
    )
    op.create_unique_constraint(
        "uq_rag_knowledge_index_member_snapshot_evidence_key",
        TABLE,
        ["knowledge_index_id", "source_snapshot_id", "evidence_key"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE})")):
        raise RuntimeError("downgrade would lose authoritative evidence_key bindings")

    op.drop_constraint("uq_rag_knowledge_index_member_snapshot_evidence_key", TABLE, type_="unique")
    op.drop_constraint("chk_rag_knowledge_index_member_evidence_key_nfc", TABLE, type_="check")
    op.drop_constraint("chk_rag_knowledge_index_member_evidence_key_nonblank", TABLE, type_="check")
    op.drop_column(TABLE, "evidence_key")
