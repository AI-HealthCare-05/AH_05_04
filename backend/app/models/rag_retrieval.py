from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.async_jobs import AiJob
    from app.models.knowledge import KnowledgeChunk, RagKnowledgeIndex


class RetrievalRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RetrievalRunVariant(StrEnum):
    RET_L = "RET-L"
    RET_D = "RET-D"
    RET_H = "RET-H"


class RetrievalSignalMethod(StrEnum):
    EXACT = "EXACT"
    TRIGRAM = "TRIGRAM"
    FTS = "FTS"
    LEXICAL = "LEXICAL"
    DENSE = "DENSE"


class RetrievalRun(Base):
    __tablename__ = "retrieval_run"
    __table_args__ = (
        UniqueConstraint("job_id", "node_id", name="uq_retrieval_run_job_node"),
        CheckConstraint(
            "variant IN ('RET-L', 'RET-D', 'RET-H')",
            name="chk_retrieval_run_variant",
        ),
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')",
            name="chk_retrieval_run_status",
        ),
        CheckConstraint(
            "lexical_limit > 0 AND dense_limit > 0 AND hybrid_limit > 0 AND final_k > 0",
            name="chk_retrieval_run_limits",
        ),
        CheckConstraint(
            "runtime_release_bundle_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_bundle_hash",
        ),
        CheckConstraint(
            "runtime_execution_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_manifest_hash",
        ),
        CheckConstraint(
            "query_digest ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_query_digest",
        ),
        CheckConstraint(
            "filter_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_filter_snapshot_hash",
        ),
        CheckConstraint(
            "source_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_source_manifest_hash",
        ),
        CheckConstraint(
            "retrieval_configuration_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_retrieval_configuration_hash",
        ),
        CheckConstraint(
            "query_embedding_sha256 IS NULL OR query_embedding_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_query_embedding_sha256",
        ),
        CheckConstraint(
            "search_receipt_hash IS NULL OR search_receipt_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_search_receipt_hash",
        ),
        CheckConstraint(
            "terminal_replay_payload_hash IS NULL OR terminal_replay_payload_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_terminal_replay_payload_hash",
        ),
        CheckConstraint(
            "(terminal_replay_payload IS NULL) = (terminal_replay_payload_hash IS NULL)",
            name="chk_retrieval_run_terminal_replay_payload_pair",
        ),
        CheckConstraint(
            "receipt_hash IS NULL OR receipt_hash ~ '^[0-9a-f]{64}$'",
            name="chk_retrieval_run_receipt_hash",
        ),
        Index("idx_retrieval_run_job_id", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("ai_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    execution_context_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    prescription_version_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    runtime_release_bundle_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    runtime_release_bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_execution_manifest_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    runtime_execution_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_guard_decision_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    knowledge_index_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_knowledge_index.id", ondelete="RESTRICT"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(String(80), nullable=False, default="hybrid_retrieve")
    variant: Mapped[str] = mapped_column(String(20), nullable=False)
    query_digest_algorithm: Mapped[str] = mapped_column(String(80), nullable=False)
    query_digest_key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    query_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    filter_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    filter_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    query_embedding_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lexical_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    dense_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    hybrid_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    final_k: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RUNNING")
    diagnostic_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    search_receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminal_replay_payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    terminal_replay_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[AiJob] = relationship()
    knowledge_index: Mapped[RagKnowledgeIndex] = relationship()
    signals: Mapped[list[RetrievalSignal]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    hits: Mapped[list[RetrievalHit]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RetrievalSignal(Base):
    __tablename__ = "retrieval_signal"
    __table_args__ = (
        PrimaryKeyConstraint(
            "retrieval_run_id",
            "retrieval_method",
            "knowledge_chunk_id",
            name="pk_retrieval_signal",
        ),
        UniqueConstraint(
            "retrieval_run_id",
            "retrieval_method",
            "raw_rank",
            name="uq_retrieval_signal_run_method_rank",
        ),
        CheckConstraint(
            "retrieval_method IN ('EXACT', 'TRIGRAM', 'FTS', 'LEXICAL', 'DENSE')",
            name="chk_retrieval_signal_method",
        ),
        CheckConstraint("raw_rank > 0", name="chk_retrieval_signal_raw_rank"),
        Index("idx_retrieval_signal_run_id", "retrieval_run_id"),
    )

    retrieval_run_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("retrieval_run.id", ondelete="CASCADE"),
        primary_key=True,
    )
    retrieval_method: Mapped[str] = mapped_column(String(20), primary_key=True)
    knowledge_chunk_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("knowledge_chunk.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    raw_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    score_projection_version: Mapped[str] = mapped_column(String(80), nullable=False)

    run: Mapped[RetrievalRun] = relationship(back_populates="signals")
    knowledge_chunk: Mapped[KnowledgeChunk] = relationship()


class RetrievalHit(Base):
    __tablename__ = "retrieval_hit"
    __table_args__ = (
        PrimaryKeyConstraint(
            "retrieval_run_id",
            "knowledge_chunk_id",
            name="pk_retrieval_hit",
        ),
        UniqueConstraint("retrieval_run_id", "rrf_rank", name="uq_retrieval_hit_run_rrf_rank"),
        UniqueConstraint("retrieval_run_id", "final_rank", name="uq_retrieval_hit_run_final_rank"),
        CheckConstraint("rrf_rank > 0", name="chk_retrieval_hit_rrf_rank"),
        CheckConstraint("final_rank > 0", name="chk_retrieval_hit_final_rank"),
        CheckConstraint("lexical_rank IS NULL OR lexical_rank > 0", name="chk_retrieval_hit_lexical_rank"),
        CheckConstraint("dense_rank IS NULL OR dense_rank > 0", name="chk_retrieval_hit_dense_rank"),
        Index("idx_retrieval_hit_run_selected", "retrieval_run_id", "selected"),
    )

    retrieval_run_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("retrieval_run.id", ondelete="CASCADE"),
        primary_key=True,
    )
    knowledge_chunk_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("knowledge_chunk.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    lexical_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dense_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rrf_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    rrf_score: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    rrf_score_numerator: Mapped[str] = mapped_column(String(64), nullable=False)
    rrf_score_denominator: Mapped[str] = mapped_column(String(64), nullable=False)
    rerank_score: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    final_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)

    run: Mapped[RetrievalRun] = relationship(back_populates="hits")
    knowledge_chunk: Mapped[KnowledgeChunk] = relationship()
