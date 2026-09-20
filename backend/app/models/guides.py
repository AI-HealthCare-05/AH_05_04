from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
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
        ForeignKeyConstraint(
            ["prescription_version_id", "prescription_id"],
            ["prescription_version.id", "prescription_version.prescription_id"],
            name="fk_guide_prescription_version_prescription",
        ),
        Index("idx_guide_prescription_requested", "prescription_id", "requested_at", "id"),
        Index("idx_guide_profile_requested", "profile_id", "requested_at", "id"),
        UniqueConstraint("ai_job_id", name="uq_guide_ai_job"),
        CheckConstraint(
            "generation_status IN ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED')",
            name="chk_guide_generation_status",
        ),
        CheckConstraint(
            "(release_projection_version IS NULL AND release_decision IS NULL "
            "AND release_is_current IS NULL AND answer_claim_action_texts IS NULL "
            "AND answer_uncertainty_text IS NULL AND answer_consultation_text IS NULL "
            "AND fallback_code IS NULL AND fallback_text IS NULL) OR "
            "(release_projection_version IS NOT NULL AND release_decision IS NOT NULL "
            "AND release_is_current IS NOT NULL AND generation_status = 'COMPLETED' "
            "AND completed_at IS NOT NULL AND "
            "((release_decision = 'PASS' AND release_is_current AND content IS NOT NULL "
            "AND answer_claim_action_texts IS NOT NULL "
            "AND jsonb_typeof(answer_claim_action_texts) = 'array' "
            "AND jsonb_array_length(answer_claim_action_texts) > 0 "
            "AND answer_uncertainty_text IS NOT NULL AND answer_consultation_text IS NOT NULL "
            "AND fallback_code IS NULL AND fallback_text IS NULL) OR "
            "(release_decision IN ('LIMITED', 'REJECTED') AND release_is_current AND content IS NULL "
            "AND answer_claim_action_texts IS NULL AND answer_uncertainty_text IS NULL "
            "AND answer_consultation_text IS NULL AND fallback_code IS NOT NULL "
            "AND fallback_text IS NOT NULL) OR "
            "(release_decision = 'STALE' AND NOT release_is_current AND content IS NULL "
            "AND answer_claim_action_texts IS NULL AND answer_uncertainty_text IS NULL "
            "AND answer_consultation_text IS NULL AND fallback_code IS NOT NULL "
            "AND fallback_text IS NOT NULL)))",
            name="chk_guide_release_projection_shape",
        ),
        CheckConstraint(
            "release_projection_version IS NULL OR release_projection_version = 'guide-runtime-release-projection-v1'",
            name="chk_guide_release_projection_version",
        ),
        CheckConstraint(
            "fallback_code IS NULL OR fallback_code IN "
            "('NO_APPROVED_EVIDENCE', 'CONFLICTING_EVIDENCE', 'PROVIDER_TIMEOUT', "
            "'DEPENDENCY_UNAVAILABLE', 'VALIDATION_FAILED', 'PRESCRIPTION_STALE', "
            "'EXECUTION_CONTEXT_STALE', 'UNSUPPORTED_REQUEST')",
            name="chk_guide_release_fallback_code",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    prescription_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    prescription_version_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
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
    release_projection_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    release_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    release_is_current: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    answer_claim_action_texts: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    answer_uncertainty_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_consultation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fallback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    citations: Mapped[list["GuideCitation"]] = relationship(
        back_populates="guide",
        order_by="GuideCitation.display_order",
    )


class GuideCitation(Base):
    __tablename__ = "guide_citation"
    __table_args__ = (
        UniqueConstraint("guide_id", "display_order", name="uq_guide_citation_order"),
        CheckConstraint("display_order > 0", name="chk_guide_citation_order"),
        CheckConstraint(
            "(knowledge_chunk_id IS NOT NULL AND claim_text IS NOT NULL AND cited_text IS NOT NULL "
            "AND card_target_ref IS NULL AND claim_key IS NULL AND evidence_key IS NULL "
            "AND source_type IS NULL AND source_snapshot_id IS NULL "
            "AND source_snapshot_member_id IS NULL AND source_code IS NULL "
            "AND source_version IS NULL AND locator IS NULL AND content_sha256 IS NULL) OR "
            "(knowledge_chunk_id IS NULL AND claim_text IS NULL AND cited_text IS NULL "
            "AND card_target_ref IS NOT NULL AND claim_key IS NOT NULL AND evidence_key IS NOT NULL "
            "AND source_type IS NOT NULL AND source_snapshot_id IS NOT NULL "
            "AND source_snapshot_member_id IS NOT NULL AND source_code IS NOT NULL "
            "AND source_version IS NOT NULL AND locator IS NOT NULL AND content_sha256 IS NOT NULL)",
            name="chk_guide_citation_variant",
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_guide_citation_content_hash",
        ),
        CheckConstraint(
            "source_type IS NULL OR source_type = 'LIFESTYLE_GUIDELINE'",
            name="chk_guide_citation_source_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    guide_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("guide.id"), nullable=False)
    knowledge_chunk_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("knowledge_chunk.id"),
        nullable=True,
    )
    claim_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cited_text: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    card_target_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    claim_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_snapshot_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_source_snapshot.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_snapshot_member_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_source_snapshot_member.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    locator: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    guide: Mapped["Guide"] = relationship(back_populates="citations")
    knowledge_chunk: Mapped["KnowledgeChunk | None"] = relationship(back_populates="guide_citations")
