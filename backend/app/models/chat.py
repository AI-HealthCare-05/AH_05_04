from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.knowledge import KnowledgeChunk
    from app.models.prescriptions import Prescription
    from app.models.profiles import Profile


class ChatSessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class ChatRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class ChatGenerationStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ChatSession(Base):
    __tablename__ = "chat_session"
    __table_args__ = (
        Index(
            "idx_chat_session_prescription_activity",
            "prescription_id",
            "session_status",
            "last_message_at",
            "id",
        ),
        Index("idx_chat_session_profile_activity", "profile_id", "last_message_at", "id"),
        CheckConstraint("session_status IN ('ACTIVE', 'CLOSED')", name="chk_chat_session_status"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    prescription_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("prescription.id"), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("profile.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    session_status: Mapped[ChatSessionStatus] = mapped_column(
        Enum(ChatSessionStatus, native_enum=False, length=20),
        nullable=False,
        default=ChatSessionStatus.ACTIVE,
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    prescription: Mapped["Prescription"] = relationship(back_populates="chat_sessions")
    profile: Mapped["Profile"] = relationship()
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session")


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        UniqueConstraint("session_id", "message_seq", name="uq_chat_message_session_seq"),
        CheckConstraint("role IN ('USER', 'ASSISTANT')", name="chk_chat_message_role"),
        CheckConstraint(
            "generation_status IN ('NOT_APPLICABLE', 'PENDING', 'GENERATING', 'COMPLETED', 'FAILED')",
            name="chk_chat_message_generation_status",
        ),
        CheckConstraint("message_seq > 0", name="chk_chat_message_seq"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("chat_session.id"), nullable=False)
    message_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[ChatRole] = mapped_column(Enum(ChatRole, native_enum=False, length=20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_status: Mapped[ChatGenerationStatus] = mapped_column(
        Enum(ChatGenerationStatus, native_enum=False, length=20),
        nullable=False,
        default=ChatGenerationStatus.NOT_APPLICABLE,
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
    citations: Mapped[list["ChatCitation"]] = relationship(back_populates="message")


class ChatCitation(Base):
    __tablename__ = "chat_citation"
    __table_args__ = (
        UniqueConstraint("message_id", "display_order", name="uq_chat_citation_order"),
        CheckConstraint("display_order > 0", name="chk_chat_citation_order"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("chat_message.id"), nullable=False)
    knowledge_chunk_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("knowledge_chunk.id"), nullable=False)
    claim_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    cited_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    message: Mapped["ChatMessage"] = relationship(back_populates="citations")
    knowledge_chunk: Mapped["KnowledgeChunk"] = relationship(back_populates="chat_citations")
