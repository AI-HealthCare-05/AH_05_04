"""Add the versioned Knowledge Evidence Index foundation.

Fixed-zero lock markers let the Builder acquire PostgreSQL row locks without
granting UPDATE on provenance or business columns.

Revision ID: 178a1b2c3d4e
Revises: 166f30415263
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision = "178a1b2c3d4e"
down_revision = "166f30415263"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    for table in ("rag_source", "rag_source_endpoint", "rag_source_operation"):
        op.add_column(
            table,
            sa.Column("knowledge_index_lock_marker", sa.Integer(), server_default="0", nullable=False),
        )
        op.create_check_constraint(
            f"chk_{table}_knowledge_index_lock_marker",
            table,
            "knowledge_index_lock_marker = 0",
        )
    for table in ("rag_source_ingestion_run", "rag_source_ingestion_artifact"):
        op.add_column(
            table,
            sa.Column("knowledge_index_lock_marker", sa.Integer(), server_default="0", nullable=False),
        )
        op.create_check_constraint(
            f"chk_{table}_knowledge_index_lock_marker",
            table,
            "knowledge_index_lock_marker = 0",
        )

    op.create_table(
        "rag_source_snapshot_member",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("member_kind", sa.String(length=30), nullable=False),
        sa.Column("endpoint_id", sa.CHAR(length=36), nullable=True),
        sa.Column("operation_id", sa.CHAR(length=36), nullable=True),
        sa.Column("ingestion_artifact_id", sa.CHAR(length=36), nullable=True),
        sa.Column("locator", sa.String(length=500), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("knowledge_index_lock_marker", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(member_kind = 'ENDPOINT_OPERATION' AND endpoint_id IS NOT NULL "
            "AND ingestion_artifact_id IS NULL) OR "
            "(member_kind = 'ARTIFACT' AND endpoint_id IS NULL "
            "AND operation_id IS NULL AND ingestion_artifact_id IS NOT NULL)",
            name="chk_rag_source_snapshot_member_origin",
        ),
        sa.CheckConstraint("length(locator) BETWEEN 1 AND 500", name="chk_rag_source_snapshot_member_locator_length"),
        sa.CheckConstraint("locator !~ '[[:cntrl:]]'", name="chk_rag_source_snapshot_member_locator_control"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_source_snapshot_member_content_hash"),
        sa.CheckConstraint(
            "knowledge_index_lock_marker = 0",
            name="chk_rag_source_snapshot_member_knowledge_index_lock_marker",
        ),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["rag_source_snapshot.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["endpoint_id"], ["rag_source_endpoint.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operation_id"], ["rag_source_operation.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ingestion_artifact_id"], ["rag_source_ingestion_artifact.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_snapshot_id", "member_kind", "locator", "content_sha256", name="uq_rag_source_snapshot_member"
        ),
    )
    op.create_index("idx_rag_source_snapshot_member_snapshot", "rag_source_snapshot_member", ["source_snapshot_id"])

    op.add_column(
        "knowledge_document",
        sa.Column("record_contract_version", sa.String(length=40), server_default="LEGACY_V1", nullable=False),
    )
    op.add_column("knowledge_document", sa.Column("source_snapshot_member_id", sa.CHAR(length=36), nullable=True))
    op.add_column("knowledge_document", sa.Column("external_document_id", sa.String(length=300), nullable=True))
    op.add_column("knowledge_document", sa.Column("document_content_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "knowledge_document", sa.Column("canonicalization_spec_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "knowledge_document",
        sa.Column("knowledge_index_lock_marker", sa.Integer(), server_default="0", nullable=False),
    )
    op.alter_column("knowledge_document", "publisher", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("knowledge_document", "source_url", existing_type=sa.String(length=500), nullable=True)
    op.alter_column("knowledge_document", "document_version", existing_type=sa.String(length=100), nullable=True)
    op.create_foreign_key(
        "fk_knowledge_document_snapshot_member",
        "knowledge_document",
        "rag_source_snapshot_member",
        ["source_snapshot_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_knowledge_document_evidence_identity",
        "knowledge_document",
        ["source_snapshot_member_id", "external_document_id"],
    )
    op.create_check_constraint(
        "chk_knowledge_document_contract_shape",
        "knowledge_document",
        "(record_contract_version = 'LEGACY_V1' AND publisher IS NOT NULL "
        "AND source_url IS NOT NULL AND document_version IS NOT NULL "
        "AND source_snapshot_member_id IS NULL AND external_document_id IS NULL "
        "AND document_content_hash IS NULL AND canonicalization_spec_version IS NULL) OR "
        "(record_contract_version = 'KNOWLEDGE_EVIDENCE_V1' "
        "AND source_snapshot_member_id IS NOT NULL AND external_document_id IS NOT NULL "
        "AND document_content_hash ~ '^[0-9a-f]{64}$' "
        "AND length(trim(canonicalization_spec_version)) > 0)",
    )
    op.create_check_constraint(
        "chk_knowledge_document_knowledge_index_lock_marker",
        "knowledge_document",
        "knowledge_index_lock_marker = 0",
    )

    op.add_column("knowledge_chunk", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("knowledge_chunk", sa.Column("normalization_version", sa.String(length=100), nullable=True))
    op.add_column(
        "knowledge_chunk",
        sa.Column("knowledge_index_lock_marker", sa.Integer(), server_default="0", nullable=False),
    )
    op.alter_column("knowledge_chunk", "embedding_model", existing_type=sa.String(length=100), nullable=True)
    op.alter_column("knowledge_chunk", "vector_store_key", existing_type=sa.String(length=255), nullable=True)
    op.create_check_constraint(
        "chk_knowledge_chunk_evidence_shape",
        "knowledge_chunk",
        "(content_hash IS NULL AND normalization_version IS NULL) OR "
        "(content_hash ~ '^[0-9a-f]{64}$' AND length(trim(normalization_version)) > 0)",
    )
    op.create_check_constraint(
        "chk_knowledge_chunk_knowledge_index_lock_marker",
        "knowledge_chunk",
        "knowledge_index_lock_marker = 0",
    )

    op.create_table(
        "rag_knowledge_index",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("index_code", sa.String(length=120), nullable=False),
        sa.Column("index_version", sa.String(length=80), nullable=False),
        sa.Column("corpus_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("index_configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_model_ref", sa.String(length=255), nullable=False),
        sa.Column("embedding_model_version", sa.String(length=80), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(length=20), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("knowledge_index_lock_marker", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("length(trim(index_code)) > 0", name="chk_rag_knowledge_index_code_nonblank"),
        sa.CheckConstraint("length(trim(index_version)) > 0", name="chk_rag_knowledge_index_version_nonblank"),
        sa.CheckConstraint("corpus_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_corpus_hash"),
        sa.CheckConstraint("embedding_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_embedding_hash"),
        sa.CheckConstraint(
            "index_configuration_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_configuration_hash"
        ),
        sa.CheckConstraint(
            "embedding_dimension BETWEEN 1 AND 2000", name="chk_rag_knowledge_index_embedding_dimension"
        ),
        sa.CheckConstraint("distance_metric = 'COSINE'", name="chk_rag_knowledge_index_distance_metric"),
        sa.CheckConstraint("member_count >= 0", name="chk_rag_knowledge_index_member_count"),
        sa.CheckConstraint(
            "knowledge_index_lock_marker = 0",
            name="chk_rag_knowledge_index_knowledge_index_lock_marker",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("index_code", "index_version", name="uq_rag_knowledge_index_version"),
    )

    op.create_table(
        "rag_knowledge_index_member",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_index_id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_chunk_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_member_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_code", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=False),
        sa.Column("canonical_checksum", sa.String(length=64), nullable=False),
        sa.Column("external_document_id", sa.String(length=300), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column("embedding_sha256", sa.String(length=64), nullable=False),
        sa.Column("member_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="chk_rag_knowledge_index_member_chunk_index"),
        sa.CheckConstraint("member_order > 0", name="chk_rag_knowledge_index_member_order"),
        sa.CheckConstraint("canonical_checksum ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_member_checksum"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_member_content_hash"),
        sa.CheckConstraint("embedding_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_member_embedding_hash"),
        sa.CheckConstraint(
            "vector_dims(embedding) BETWEEN 1 AND 2000",
            name="chk_rag_knowledge_index_member_embedding_dimension",
        ),
        sa.ForeignKeyConstraint(["knowledge_index_id"], ["rag_knowledge_index.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["knowledge_chunk_id"], ["knowledge_chunk.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["rag_source_snapshot.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_snapshot_member_id"], ["rag_source_snapshot_member.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_index_id", "member_order", name="uq_rag_knowledge_index_member_order"),
        sa.UniqueConstraint("knowledge_index_id", "knowledge_chunk_id", name="uq_rag_knowledge_index_member_chunk"),
        sa.UniqueConstraint(
            "knowledge_index_id",
            "source_code",
            "source_version",
            "external_document_id",
            "chunk_index",
            name="uq_rag_knowledge_index_member_coordinate",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE rag_knowledge_index_member IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE rag_knowledge_index IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE rag_source_snapshot_member IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE knowledge_document IN SHARE ROW EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE knowledge_chunk IN SHARE ROW EXCLUSIVE MODE"))
    incompatible = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM rag_knowledge_index_member) "
            "OR EXISTS (SELECT 1 FROM rag_knowledge_index) "
            "OR EXISTS (SELECT 1 FROM rag_source_snapshot_member) "
            "OR EXISTS (SELECT 1 FROM knowledge_document "
            "WHERE record_contract_version = 'KNOWLEDGE_EVIDENCE_V1') "
            "OR EXISTS (SELECT 1 FROM knowledge_chunk "
            "WHERE content_hash IS NOT NULL OR normalization_version IS NOT NULL)"
        )
    )
    if incompatible:
        raise RuntimeError("downgrade would lose Knowledge Evidence Index data")

    op.drop_table("rag_knowledge_index_member")
    op.drop_table("rag_knowledge_index")
    op.drop_constraint("chk_knowledge_chunk_evidence_shape", "knowledge_chunk", type_="check")
    op.alter_column("knowledge_chunk", "vector_store_key", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("knowledge_chunk", "embedding_model", existing_type=sa.String(length=100), nullable=False)
    op.drop_column("knowledge_chunk", "normalization_version")
    op.drop_column("knowledge_chunk", "content_hash")
    op.drop_column("knowledge_chunk", "knowledge_index_lock_marker")
    op.drop_constraint("chk_knowledge_document_contract_shape", "knowledge_document", type_="check")
    op.drop_constraint("uq_knowledge_document_evidence_identity", "knowledge_document", type_="unique")
    op.drop_constraint("fk_knowledge_document_snapshot_member", "knowledge_document", type_="foreignkey")
    op.alter_column("knowledge_document", "document_version", existing_type=sa.String(length=100), nullable=False)
    op.alter_column("knowledge_document", "source_url", existing_type=sa.String(length=500), nullable=False)
    op.alter_column("knowledge_document", "publisher", existing_type=sa.String(length=255), nullable=False)
    op.drop_column("knowledge_document", "canonicalization_spec_version")
    op.drop_column("knowledge_document", "document_content_hash")
    op.drop_column("knowledge_document", "external_document_id")
    op.drop_column("knowledge_document", "source_snapshot_member_id")
    op.drop_column("knowledge_document", "record_contract_version")
    op.drop_column("knowledge_document", "knowledge_index_lock_marker")
    op.drop_index("idx_rag_source_snapshot_member_snapshot", table_name="rag_source_snapshot_member")
    op.drop_table("rag_source_snapshot_member")
    for table in (
        "rag_source_ingestion_artifact",
        "rag_source_ingestion_run",
        "rag_source_operation",
        "rag_source_endpoint",
        "rag_source",
    ):
        op.drop_column(table, "knowledge_index_lock_marker")
