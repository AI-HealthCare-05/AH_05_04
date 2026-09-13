"""add rag source reject artifacts

Revision ID: 165b5c4d3e2f
Revises: 165a4b3c2d1e
Create Date: 2026-09-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "165b5c4d3e2f"
down_revision: str | Sequence[str] | None = "165a4b3c2d1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rag_source_ingestion_artifact",
        sa.Column(
            "artifact_kind",
            sa.String(length=20),
            nullable=False,
            server_default="RAW_RESPONSE",
        ),
    )
    op.add_column(
        "rag_source_ingestion_artifact",
        sa.Column("reject_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "rag_source_ingestion_artifact",
        sa.Column("parser_location", sa.String(length=255), nullable=True),
    )
    op.alter_column(
        "rag_source_ingestion_artifact",
        "page_number",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_check_constraint(
        "chk_rag_source_artifact_kind_metadata",
        "rag_source_ingestion_artifact",
        "(artifact_kind = 'RAW_RESPONSE' AND page_number IS NOT NULL "
        "AND reject_code IS NULL AND parser_location IS NULL) OR "
        "(artifact_kind = 'REJECTS' AND page_number IS NULL "
        "AND reject_code ~ '^[A-Z][A-Z0-9_]{0,99}$' "
        "AND length(trim(parser_location)) > 0 "
        "AND parser_location !~ '[[:cntrl:]]')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_ingestion_artifact IN ACCESS EXCLUSIVE MODE"))
    rejection_count = connection.execute(
        sa.text("SELECT count(*) FROM rag_source_ingestion_artifact WHERE artifact_kind = 'REJECTS'")
    ).scalar_one()
    if rejection_count:
        raise RuntimeError(
            "Cannot downgrade revision 165b5c4d3e2f while REJECTS artifacts exist. "
            "Use a forward-fix migration or an approved retention procedure instead."
        )

    op.drop_constraint(
        "chk_rag_source_artifact_kind_metadata",
        "rag_source_ingestion_artifact",
        type_="check",
    )
    op.alter_column(
        "rag_source_ingestion_artifact",
        "page_number",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("rag_source_ingestion_artifact", "parser_location")
    op.drop_column("rag_source_ingestion_artifact", "reject_code")
    op.drop_column("rag_source_ingestion_artifact", "artifact_kind")
