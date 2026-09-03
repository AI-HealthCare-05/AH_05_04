from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.knowledge import KnowledgeChunk
    from app.models.prescriptions import Prescription
    from app.models.profiles import Profile


class GuideGenerationStatus(StrEnum):
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Guide(Base):
    __tablename__ = "guide"
    __table_args__ = (
        ForeignKeyConstraint(
            ["prescription_id", "profile_id"],
            ["prescription.id", "prescription.profile_id"],
            name="fk_guide_prescription_profile",
        ),
        Index("idx_guide_prescription_requested", "prescription_id", "requested_at", "id"),
        Index("idx_guide_profile_requested", "profile_id", "requested_at", "id"),
        UniqueConstraint("ai_job_id", name="uq_guide_ai_job"),
        CheckConstraint(
            "generation_status IN ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED')",
            name="chk_guide_generation_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    prescription_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("profile.id"), nullable=False)
    ai_job_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "ai_job.id",
            name="fk_guide_ai_job",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    generation_status: Mapped[GuideGenerationStatus] = mapped_column(
        Enum(GuideGenerationStatus, native_enum=False, length=20),
        nullable=False,
        default=GuideGenerationStatus.PENDING,
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    prescription: Mapped["Prescription"] = relationship(back_populates="guides")
    profile: Mapped["Profile"] = relationship(overlaps="prescription")
    citations: Mapped[list["GuideCitation"]] = relationship(back_populates="guide")


class GuideCitation(Base):
    __tablename__ = "guide_citation"
    __table_args__ = (
        UniqueConstraint("guide_id", "display_order", name="uq_guide_citation_order"),
        CheckConstraint("display_order > 0", name="chk_guide_citation_order"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    guide_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("guide.id"), nullable=False)
    knowledge_chunk_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("knowledge_chunk.id"), nullable=False)
    claim_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    cited_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    guide: Mapped["Guide"] = relationship(back_populates="citations")
    knowledge_chunk: Mapped["KnowledgeChunk"] = relationship(back_populates="guide_citations")
