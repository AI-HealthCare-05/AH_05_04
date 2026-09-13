from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.chat import ChatCitation
    from app.models.guides import GuideCitation


class KnowledgeDocumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class KnowledgeDocumentContractVersion(StrEnum):
    LEGACY_V1 = "LEGACY_V1"
    KNOWLEDGE_EVIDENCE_V1 = "KNOWLEDGE_EVIDENCE_V1"


class RagKnowledgeDistanceMetric(StrEnum):
    COSINE = "COSINE"


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_document"
    __table_args__ = (
        UniqueConstraint("source_url", "document_version", name="uq_knowledge_document_source_version"),
        UniqueConstraint(
            "source_snapshot_member_id",
            "external_document_id",
            name="uq_knowledge_document_evidence_identity",
        ),
        CheckConstraint("document_status IN ('ACTIVE', 'INACTIVE')", name="chk_knowledge_document_status"),
        CheckConstraint(
            "(record_contract_version = 'LEGACY_V1' AND publisher IS NOT NULL "
            "AND source_url IS NOT NULL AND document_version IS NOT NULL "
            "AND source_snapshot_member_id IS NULL AND external_document_id IS NULL "
            "AND document_content_hash IS NULL AND canonicalization_spec_version IS NULL) OR "
            "(record_contract_version = 'KNOWLEDGE_EVIDENCE_V1' "
            "AND source_snapshot_member_id IS NOT NULL AND external_document_id IS NOT NULL "
            "AND document_content_hash ~ '^[0-9a-f]{64}$' "
            "AND length(trim(canonicalization_spec_version)) > 0)",
            name="chk_knowledge_document_contract_shape",
        ),
        CheckConstraint("knowledge_index_lock_marker = 0", name="chk_knowledge_document_knowledge_index_lock_marker"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # utf8mb4 기준 (source_url + document_version) 복합 UNIQUE 인덱스가 MySQL의
    # 인덱스 최대 길이(3072바이트)를 넘지 않도록 500자로 제한합니다.
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    record_contract_version: Mapped[KnowledgeDocumentContractVersion] = mapped_column(
        Enum(KnowledgeDocumentContractVersion, native_enum=False, length=40),
        nullable=False,
        default=KnowledgeDocumentContractVersion.LEGACY_V1,
        server_default=KnowledgeDocumentContractVersion.LEGACY_V1.value,
    )
    source_snapshot_member_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot_member.id", ondelete="RESTRICT"), nullable=True
    )
    external_document_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    document_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonicalization_spec_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    knowledge_index_lock_marker: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    document_status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        Enum(KnowledgeDocumentStatus, native_enum=False, length=20),
        nullable=False,
        default=KnowledgeDocumentStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="knowledge_document")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        UniqueConstraint("knowledge_document_id", "chunk_index", name="uq_knowledge_chunk_order"),
        CheckConstraint("chunk_index >= 0", name="chk_knowledge_chunk_index"),
        CheckConstraint(
            "(content_hash IS NULL AND normalization_version IS NULL) OR "
            "(content_hash ~ '^[0-9a-f]{64}$' AND length(trim(normalization_version)) > 0)",
            name="chk_knowledge_chunk_evidence_shape",
        ),
        CheckConstraint("knowledge_index_lock_marker = 0", name="chk_knowledge_chunk_knowledge_index_lock_marker"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    knowledge_document_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("knowledge_document.id"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 외부 벡터 저장소(Qdrant 등)의 point ID
    vector_store_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalization_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    knowledge_index_lock_marker: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    knowledge_document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
    guide_citations: Mapped[list["GuideCitation"]] = relationship(back_populates="knowledge_chunk")
    chat_citations: Mapped[list["ChatCitation"]] = relationship(back_populates="knowledge_chunk")


class RagKnowledgeIndex(Base):
    __tablename__ = "rag_knowledge_index"
    __table_args__ = (
        UniqueConstraint("index_code", "index_version", name="uq_rag_knowledge_index_version"),
        CheckConstraint("length(trim(index_code)) > 0", name="chk_rag_knowledge_index_code_nonblank"),
        CheckConstraint("length(trim(index_version)) > 0", name="chk_rag_knowledge_index_version_nonblank"),
        CheckConstraint("corpus_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_corpus_hash"),
        CheckConstraint("embedding_manifest_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_embedding_hash"),
        CheckConstraint(
            "index_configuration_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_configuration_hash"
        ),
        CheckConstraint("embedding_dimension BETWEEN 1 AND 2000", name="chk_rag_knowledge_index_embedding_dimension"),
        CheckConstraint("distance_metric = 'COSINE'", name="chk_rag_knowledge_index_distance_metric"),
        CheckConstraint("member_count >= 0", name="chk_rag_knowledge_index_member_count"),
        CheckConstraint("knowledge_index_lock_marker = 0", name="chk_rag_knowledge_index_knowledge_index_lock_marker"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    index_code: Mapped[str] = mapped_column(String(120), nullable=False)
    index_version: Mapped[str] = mapped_column(String(80), nullable=False)
    corpus_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    index_configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_metric: Mapped[RagKnowledgeDistanceMetric] = mapped_column(
        Enum(RagKnowledgeDistanceMetric, native_enum=False, length=20), nullable=False
    )
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_index_lock_marker: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagKnowledgeIndexMember(Base):
    __tablename__ = "rag_knowledge_index_member"
    __table_args__ = (
        UniqueConstraint("knowledge_index_id", "member_order", name="uq_rag_knowledge_index_member_order"),
        UniqueConstraint("knowledge_index_id", "knowledge_chunk_id", name="uq_rag_knowledge_index_member_chunk"),
        UniqueConstraint(
            "knowledge_index_id",
            "source_code",
            "source_version",
            "external_document_id",
            "chunk_index",
            name="uq_rag_knowledge_index_member_coordinate",
        ),
        CheckConstraint("chunk_index >= 0", name="chk_rag_knowledge_index_member_chunk_index"),
        CheckConstraint("member_order > 0", name="chk_rag_knowledge_index_member_order"),
        CheckConstraint("canonical_checksum ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_member_checksum"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_member_content_hash"),
        CheckConstraint("embedding_sha256 ~ '^[0-9a-f]{64}$'", name="chk_rag_knowledge_index_member_embedding_hash"),
        CheckConstraint(
            "vector_dims(embedding) BETWEEN 1 AND 2000", name="chk_rag_knowledge_index_member_embedding_dimension"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    knowledge_index_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_knowledge_index.id", ondelete="RESTRICT"), nullable=False
    )
    knowledge_chunk_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("knowledge_chunk.id", ondelete="RESTRICT"), nullable=False
    )
    source_snapshot_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    source_snapshot_member_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot_member.id", ondelete="RESTRICT"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    external_document_id: Mapped[str] = mapped_column(String(300), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(), nullable=False)
    embedding_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    member_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
