"""add rag source ingestion artifacts

Revision ID: 165a4b3c2d1e
Revises: 164a9c8e7d6f
Create Date: 2026-09-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "165a4b3c2d1e"
down_revision: str | Sequence[str] | None = "164a9c8e7d6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_source_ingestion_artifact",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("ingestion_run_id", sa.CHAR(length=36), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("artifact_key", sa.String(length=500), nullable=False),
        sa.Column("storage_backend", sa.String(length=50), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("raw_checksum", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("page_number > 0", name="chk_rag_source_artifact_page_positive"),
        sa.CheckConstraint("length(trim(artifact_key)) > 0", name="chk_rag_source_artifact_key_nonblank"),
        sa.CheckConstraint("length(trim(storage_backend)) > 0", name="chk_rag_source_artifact_backend_nonblank"),
        sa.CheckConstraint("length(trim(object_key)) > 0", name="chk_rag_source_artifact_object_key_nonblank"),
        sa.CheckConstraint("raw_checksum ~ '^[0-9a-f]{64}$'", name="chk_rag_source_artifact_checksum"),
        sa.CheckConstraint("byte_size >= 0", name="chk_rag_source_artifact_byte_size"),
        sa.CheckConstraint("length(trim(content_type)) > 0", name="chk_rag_source_artifact_content_type_nonblank"),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["rag_source_ingestion_run.id"],
            name="fk_rag_source_artifact_ingestion_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rag_source_ingestion_artifact"),
        sa.UniqueConstraint("ingestion_run_id", "page_number", name="uq_rag_source_artifact_run_page"),
        sa.UniqueConstraint("ingestion_run_id", "artifact_key", name="uq_rag_source_artifact_run_key"),
    )
    op.create_index(
        "idx_rag_source_artifact_run",
        "rag_source_ingestion_artifact",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        "idx_rag_source_artifact_object",
        "rag_source_ingestion_artifact",
        ["storage_backend", "object_key"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_rag_source_ingestion_artifact_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'rag_source_ingestion_artifact rows are append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_rag_source_ingestion_artifact_prevent_update
            BEFORE UPDATE ON rag_source_ingestion_artifact
            FOR EACH ROW
            EXECUTE FUNCTION prevent_rag_source_ingestion_artifact_mutation()
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_rag_source_ingestion_artifact_prevent_delete
            BEFORE DELETE ON rag_source_ingestion_artifact
            FOR EACH ROW
            EXECUTE FUNCTION prevent_rag_source_ingestion_artifact_mutation()
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_source_ingestion_artifact IN ACCESS EXCLUSIVE MODE"))
    artifact_count = connection.execute(sa.text("SELECT count(*) FROM rag_source_ingestion_artifact")).scalar_one()
    if artifact_count:
        raise RuntimeError(
            "Cannot downgrade revision 165a4b3c2d1e while Source ingestion artifacts exist. "
            "Use a forward-fix migration or an approved retention procedure instead."
        )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_rag_source_ingestion_artifact_prevent_delete ON rag_source_ingestion_artifact"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_rag_source_ingestion_artifact_prevent_update ON rag_source_ingestion_artifact"
    )
    op.drop_table("rag_source_ingestion_artifact")
    op.execute("DROP FUNCTION IF EXISTS prevent_rag_source_ingestion_artifact_mutation()")
