"""Create retrieval_run, retrieval_signal, and retrieval_hit tables (#178).

Revision ID: 178c2d3e4f50
Revises: 556a1b2c3d4e
Create Date: 2026-09-15 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "178c2d3e4f50"
down_revision = "556a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retrieval_run",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("job_id", sa.CHAR(length=36), nullable=False),
        sa.Column("execution_context_id", sa.CHAR(length=36), nullable=False),
        sa.Column("prescription_version_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_release_bundle_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_release_bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_execution_manifest_id", sa.CHAR(length=36), nullable=False),
        sa.Column("runtime_execution_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("runtime_guard_decision_ref", sa.String(length=255), nullable=False),
        sa.Column("knowledge_index_id", sa.CHAR(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=80), server_default="hybrid_retrieve", nullable=False),
        sa.Column("variant", sa.String(length=20), nullable=False),
        sa.Column("query_digest_algorithm", sa.String(length=80), nullable=False),
        sa.Column("query_digest_key_version", sa.String(length=80), nullable=False),
        sa.Column("query_digest", sa.String(length=64), nullable=False),
        sa.Column("filter_snapshot", sa.JSON(), nullable=False),
        sa.Column("filter_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("retrieval_configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("query_embedding_sha256", sa.String(length=64), nullable=True),
        sa.Column("lexical_limit", sa.Integer(), nullable=False),
        sa.Column("dense_limit", sa.Integer(), nullable=False),
        sa.Column("hybrid_limit", sa.Integer(), nullable=False),
        sa.Column("final_k", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="RUNNING", nullable=False),
        sa.Column("diagnostic_code", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("search_receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["ai_job.id"], name="fk_retrieval_run_job_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knowledge_index_id"],
            ["rag_knowledge_index.id"],
            name="fk_retrieval_run_knowledge_index_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "node_id", name="uq_retrieval_run_job_node"),
        sa.CheckConstraint(
            "variant IN ('RET-L', 'RET-D', 'RET-H')",
            name="chk_retrieval_run_variant",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')",
            name="chk_retrieval_run_status",
        ),
        sa.CheckConstraint(
            "lexical_limit > 0 AND dense_limit > 0 AND hybrid_limit > 0 AND final_k > 0",
            name="chk_retrieval_run_limits",
        ),
        sa.CheckConstraint(
            "runtime_release_bundle_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_bundle_hash",
        ),
        sa.CheckConstraint(
            "runtime_execution_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_manifest_hash",
        ),
        sa.CheckConstraint(
            "query_digest ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_query_digest",
        ),
        sa.CheckConstraint(
            "filter_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_filter_snapshot_hash",
        ),
        sa.CheckConstraint(
            "source_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_source_manifest_hash",
        ),
        sa.CheckConstraint(
            "retrieval_configuration_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_retrieval_configuration_hash",
        ),
        sa.CheckConstraint(
            "query_embedding_sha256 IS NULL OR query_embedding_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_query_embedding_sha256",
        ),
        sa.CheckConstraint(
            "search_receipt_hash IS NULL OR search_receipt_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_search_receipt_hash",
        ),
        sa.CheckConstraint(
            "receipt_hash IS NULL OR receipt_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_receipt_hash",
        ),
    )
    op.create_index("idx_retrieval_run_job_id", "retrieval_run", ["job_id"])

    op.create_table(
        "retrieval_signal",
        sa.Column("retrieval_run_id", sa.CHAR(length=36), nullable=False),
        sa.Column("retrieval_method", sa.String(length=20), nullable=False),
        sa.Column("knowledge_chunk_id", sa.CHAR(length=36), nullable=False),
        sa.Column("raw_rank", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("score_projection_version", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["retrieval_run_id"],
            ["retrieval_run.id"],
            name="fk_retrieval_signal_retrieval_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_chunk_id"],
            ["knowledge_chunk.id"],
            name="fk_retrieval_signal_knowledge_chunk_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "retrieval_run_id", "retrieval_method", "knowledge_chunk_id", name="pk_retrieval_signal"
        ),
        sa.UniqueConstraint(
            "retrieval_run_id", "retrieval_method", "raw_rank", name="uq_retrieval_signal_run_method_rank"
        ),
        sa.CheckConstraint(
            "retrieval_method IN ('EXACT', 'TRIGRAM', 'FTS', 'LEXICAL', 'DENSE')",
            name="chk_retrieval_signal_method",
        ),
        sa.CheckConstraint("raw_rank > 0", name="chk_retrieval_signal_raw_rank"),
    )
    op.create_index("idx_retrieval_signal_run_id", "retrieval_signal", ["retrieval_run_id"])

    op.create_table(
        "retrieval_hit",
        sa.Column("retrieval_run_id", sa.CHAR(length=36), nullable=False),
        sa.Column("knowledge_chunk_id", sa.CHAR(length=36), nullable=False),
        sa.Column("lexical_rank", sa.Integer(), nullable=True),
        sa.Column("dense_rank", sa.Integer(), nullable=True),
        sa.Column("rrf_rank", sa.Integer(), nullable=False),
        sa.Column("rrf_score", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("rrf_score_numerator", sa.String(length=64), nullable=False),
        sa.Column("rrf_score_denominator", sa.String(length=64), nullable=False),
        sa.Column("rerank_score", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("final_rank", sa.Integer(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["retrieval_run_id"],
            ["retrieval_run.id"],
            name="fk_retrieval_hit_retrieval_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_chunk_id"],
            ["knowledge_chunk.id"],
            name="fk_retrieval_hit_knowledge_chunk_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("retrieval_run_id", "knowledge_chunk_id", name="pk_retrieval_hit"),
        sa.UniqueConstraint("retrieval_run_id", "rrf_rank", name="uq_retrieval_hit_run_rrf_rank"),
        sa.UniqueConstraint("retrieval_run_id", "final_rank", name="uq_retrieval_hit_run_final_rank"),
        sa.CheckConstraint("rrf_rank > 0", name="chk_retrieval_hit_rrf_rank"),
        sa.CheckConstraint("final_rank > 0", name="chk_retrieval_hit_final_rank"),
        sa.CheckConstraint("lexical_rank IS NULL OR lexical_rank > 0", name="chk_retrieval_hit_lexical_rank"),
        sa.CheckConstraint("dense_rank IS NULL OR dense_rank > 0", name="chk_retrieval_hit_dense_rank"),
    )
    op.create_index("idx_retrieval_hit_run_selected", "retrieval_hit", ["retrieval_run_id", "selected"])


def _has_rows(table_name: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first())


def _raise_if_retrieval_run_data_exists() -> None:
    tables = (
        "retrieval_hit",
        "retrieval_signal",
        "retrieval_run",
    )
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE"))
    non_empty = [table for table in tables if _has_rows(table)]
    if non_empty:
        raise RuntimeError("Refusing to downgrade retrieval run tables with existing data: " + ", ".join(non_empty))


def downgrade() -> None:
    _raise_if_retrieval_run_data_exists()
    op.drop_table("retrieval_hit")
    op.drop_table("retrieval_signal")
    op.drop_table("retrieval_run")
