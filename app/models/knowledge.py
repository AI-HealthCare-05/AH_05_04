from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

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


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_document"
    __table_args__ = (
        UniqueConstraint("source_url", "document_version", name="uq_knowledge_document_source_version"),
        CheckConstraint("document_status IN ('ACTIVE', 'INACTIVE')", name="chk_knowledge_document_status"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    # utf8mb4 기준 (source_url + document_version) 복합 UNIQUE 인덱스가 MySQL의
    # 인덱스 최대 길이(3072바이트)를 넘지 않도록 500자로 제한합니다.
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    document_version: Mapped[str] = mapped_column(String(100), nullable=False)
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
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    knowledge_document_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("knowledge_document.id"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    # 외부 벡터 저장소(Qdrant 등)의 point ID
    vector_store_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    knowledge_document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
    guide_citations: Mapped[list["GuideCitation"]] = relationship(back_populates="knowledge_chunk")
    chat_citations: Mapped[list["ChatCitation"]] = relationship(back_populates="knowledge_chunk")
