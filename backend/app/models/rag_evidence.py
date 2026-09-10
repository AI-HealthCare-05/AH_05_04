from collections.abc import Iterable
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.rag_catalog import RagMedicationIngredient, RagMedicationProduct
    from app.models.rag_source import RagSourceSnapshot


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class RagEvidenceKnowledgeType(StrEnum):
    SOURCE_DOCUMENT = "SOURCE_DOCUMENT"
    SOURCE_RECORD = "SOURCE_RECORD"
    SAFETY_POLICY = "SAFETY_POLICY"


class RagEvidenceType(StrEnum):
    KNOWLEDGE_CHUNK = "KNOWLEDGE_CHUNK"
    PRODUCT_FACT = "PRODUCT_FACT"
    INGREDIENT_FACT = "INGREDIENT_FACT"
    INTERACTION_RULE = "INTERACTION_RULE"
    LIFESTYLE_GUIDELINE = "LIFESTYLE_GUIDELINE"
    SAFETY_POLICY = "SAFETY_POLICY"


class RagEvidenceStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"


class RagEvidenceRuleType(StrEnum):
    INTERACTION = "INTERACTION"
    CONTRAINDICATION = "CONTRAINDICATION"
    HIGH_RISK_SYMPTOM = "HIGH_RISK_SYMPTOM"
    SAFETY_FALLBACK = "SAFETY_FALLBACK"


class RagEvidenceGuidelineType(StrEnum):
    MEDICATION_GUIDE = "MEDICATION_GUIDE"
    LIFESTYLE = "LIFESTYLE"
    LIMITED_RESPONSE = "LIMITED_RESPONSE"
    SAFETY_FALLBACK = "SAFETY_FALLBACK"


class RagCitationTargetType(StrEnum):
    GUIDE = "GUIDE"
    CHAT_MESSAGE = "CHAT_MESSAGE"
    RAG_RESULT = "RAG_RESULT"
    SAFETY_RESULT = "SAFETY_RESULT"


class RagCitationClaimKind(StrEnum):
    MEDICAL = "MEDICAL"
    AUXILIARY = "AUXILIARY"
    SAFETY_FALLBACK = "SAFETY_FALLBACK"


class RagCitationSupportStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class RagCitationAuthorizationStatus(StrEnum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class RagCitationReleaseStatus(StrEnum):
    NOT_PUBLIC = "NOT_PUBLIC"


class RagEvidenceKnowledge(Base):
    __tablename__ = "rag_evidence_knowledge"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "knowledge_key", name="uq_rag_evidence_knowledge_snapshot_key"),
        UniqueConstraint("id", "source_snapshot_id", name="uq_rag_evidence_knowledge_id_snapshot"),
        Index("idx_rag_evidence_knowledge_snapshot", "source_snapshot_id"),
        CheckConstraint("length(trim(knowledge_key)) > 0", name="chk_rag_evidence_knowledge_key_nonblank"),
        CheckConstraint("length(trim(title)) > 0", name="chk_rag_evidence_knowledge_title_nonblank"),
        CheckConstraint("length(content_digest) = 64", name="chk_rag_evidence_knowledge_digest_length"),
        CheckConstraint(
            f"knowledge_type IN ({_sql_in_list(RagEvidenceKnowledgeType)})",
            name="chk_rag_evidence_knowledge_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    knowledge_key: Mapped[str] = mapped_column(String(160), nullable=False)
    knowledge_type: Mapped[RagEvidenceKnowledgeType] = mapped_column(
        Enum(RagEvidenceKnowledgeType, native_enum=False, length=30), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_locator: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship()
    evidence_items: Mapped[list["RagEvidence"]] = relationship(back_populates="knowledge", overlaps="source_snapshot")


class RagEvidence(Base):
    __tablename__ = "rag_evidence"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", "evidence_key", name="uq_rag_evidence_snapshot_key"),
        UniqueConstraint("id", "source_snapshot_id", name="uq_rag_evidence_id_snapshot"),
        UniqueConstraint("id", "source_snapshot_id", "evidence_status", name="uq_rag_evidence_id_snapshot_status"),
        ForeignKeyConstraint(
            ["knowledge_id", "source_snapshot_id"],
            ["rag_evidence_knowledge.id", "rag_evidence_knowledge.source_snapshot_id"],
            name="fk_rag_evidence_knowledge_snapshot",
        ),
        ForeignKeyConstraint(
            ["product_id", "source_snapshot_id"],
            ["rag_medication_product.id", "rag_medication_product.source_snapshot_id"],
            name="fk_rag_evidence_product_snapshot",
        ),
        ForeignKeyConstraint(
            ["ingredient_id", "source_snapshot_id"],
            ["rag_medication_ingredient.id", "rag_medication_ingredient.source_snapshot_id"],
            name="fk_rag_evidence_ingredient_snapshot",
        ),
        Index("idx_rag_evidence_snapshot_status", "source_snapshot_id", "evidence_status"),
        CheckConstraint("length(trim(evidence_key)) > 0", name="chk_rag_evidence_key_nonblank"),
        CheckConstraint("length(evidence_digest) = 64", name="chk_rag_evidence_digest_length"),
        CheckConstraint(
            "source_locator IS NULL OR length(trim(source_locator)) > 0", name="chk_rag_evidence_locator_nonblank"
        ),
        CheckConstraint(
            "evidence_type != 'KNOWLEDGE_CHUNK' OR knowledge_id IS NOT NULL",
            name="chk_rag_evidence_knowledge_chunk_has_knowledge",
        ),
        CheckConstraint(
            "evidence_type != 'PRODUCT_FACT' OR product_id IS NOT NULL",
            name="chk_rag_evidence_product_fact_has_product",
        ),
        CheckConstraint(
            "evidence_type != 'INGREDIENT_FACT' OR ingredient_id IS NOT NULL",
            name="chk_rag_evidence_ingredient_fact_has_ingredient",
        ),
        CheckConstraint(f"evidence_type IN ({_sql_in_list(RagEvidenceType)})", name="chk_rag_evidence_type"),
        CheckConstraint(f"evidence_status IN ({_sql_in_list(RagEvidenceStatus)})", name="chk_rag_evidence_status"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    knowledge_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    product_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    ingredient_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    evidence_key: Mapped[str] = mapped_column(String(180), nullable=False)
    evidence_type: Mapped[RagEvidenceType] = mapped_column(
        Enum(RagEvidenceType, native_enum=False, length=40), nullable=False
    )
    evidence_status: Mapped[RagEvidenceStatus] = mapped_column(
        Enum(RagEvidenceStatus, native_enum=False, length=20), nullable=False
    )
    source_locator: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship(overlaps="evidence_items,knowledge,product,ingredient")
    knowledge: Mapped[RagEvidenceKnowledge | None] = relationship(
        back_populates="evidence_items", overlaps="evidence_items,source_snapshot,product,ingredient"
    )
    product: Mapped["RagMedicationProduct | None"] = relationship(
        overlaps="evidence_items,knowledge,source_snapshot,ingredient"
    )
    ingredient: Mapped["RagMedicationIngredient | None"] = relationship(
        overlaps="evidence_items,knowledge,product,source_snapshot"
    )
    rules: Mapped[list["RagEvidenceRule"]] = relationship(back_populates="evidence")
    guidelines: Mapped[list["RagEvidenceGuideline"]] = relationship(back_populates="evidence")
    citations: Mapped[list["RagCitation"]] = relationship(back_populates="evidence")


class RagEvidenceRule(Base):
    __tablename__ = "rag_evidence_rule"
    __table_args__ = (
        UniqueConstraint("evidence_id", "rule_key", name="uq_rag_evidence_rule_key"),
        CheckConstraint("length(trim(rule_key)) > 0", name="chk_rag_evidence_rule_key_nonblank"),
        CheckConstraint("length(rule_digest) = 64", name="chk_rag_evidence_rule_digest_length"),
        CheckConstraint(f"rule_type IN ({_sql_in_list(RagEvidenceRuleType)})", name="chk_rag_evidence_rule_type"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    evidence_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_evidence.id"), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_type: Mapped[RagEvidenceRuleType] = mapped_column(
        Enum(RagEvidenceRuleType, native_enum=False, length=40), nullable=False
    )
    rule_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    evidence: Mapped[RagEvidence] = relationship(back_populates="rules")


class RagEvidenceGuideline(Base):
    __tablename__ = "rag_evidence_guideline"
    __table_args__ = (
        UniqueConstraint("evidence_id", "guideline_key", name="uq_rag_evidence_guideline_key"),
        CheckConstraint("length(trim(guideline_key)) > 0", name="chk_rag_evidence_guideline_key_nonblank"),
        CheckConstraint("length(guideline_digest) = 64", name="chk_rag_evidence_guideline_digest_length"),
        CheckConstraint(
            f"guideline_type IN ({_sql_in_list(RagEvidenceGuidelineType)})", name="chk_rag_evidence_guideline_type"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    evidence_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_evidence.id"), nullable=False)
    guideline_key: Mapped[str] = mapped_column(String(160), nullable=False)
    guideline_type: Mapped[RagEvidenceGuidelineType] = mapped_column(
        Enum(RagEvidenceGuidelineType, native_enum=False, length=40), nullable=False
    )
    guideline_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    evidence: Mapped[RagEvidence] = relationship(back_populates="guidelines")


class RagCitation(Base):
    __tablename__ = "rag_citation"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", "claim_key", name="uq_rag_citation_target_claim"),
        UniqueConstraint("target_type", "target_id", "display_order", name="uq_rag_citation_display_order"),
        Index("idx_rag_citation_target", "target_type", "target_id"),
        ForeignKeyConstraint(
            ["evidence_id", "source_snapshot_id", "evidence_status"],
            ["rag_evidence.id", "rag_evidence.source_snapshot_id", "rag_evidence.evidence_status"],
            name="fk_rag_citation_evidence_snapshot_status",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_rag_citation_snapshot_version",
        ),
        Index("idx_rag_citation_evidence", "evidence_id"),
        Index("idx_rag_citation_source_snapshot", "source_snapshot_id"),
        CheckConstraint("length(trim(target_id)) > 0", name="chk_rag_citation_target_id_nonblank"),
        CheckConstraint("length(trim(claim_key)) > 0", name="chk_rag_citation_claim_key_nonblank"),
        CheckConstraint("display_order > 0", name="chk_rag_citation_display_order"),
        CheckConstraint(
            "public_excerpt IS NULL",
            name="chk_rag_citation_public_excerpt_guard_deferred",
        ),
        CheckConstraint("release_status = 'NOT_PUBLIC'", name="chk_rag_citation_public_guard_deferred"),
        CheckConstraint(
            "claim_kind != 'MEDICAL' OR support_status != 'PARTIALLY_SUPPORTED'",
            name="chk_rag_citation_medical_not_partially_supported",
        ),
        CheckConstraint(f"target_type IN ({_sql_in_list(RagCitationTargetType)})", name="chk_rag_citation_target_type"),
        CheckConstraint(f"claim_kind IN ({_sql_in_list(RagCitationClaimKind)})", name="chk_rag_citation_claim_kind"),
        CheckConstraint(
            f"support_status IN ({_sql_in_list(RagCitationSupportStatus)})", name="chk_rag_citation_support_status"
        ),
        CheckConstraint(
            f"authorization_status IN ({_sql_in_list(RagCitationAuthorizationStatus)})",
            name="chk_rag_citation_authorization_status",
        ),
        CheckConstraint(
            f"release_status IN ({_sql_in_list(RagCitationReleaseStatus)})", name="chk_rag_citation_release_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    evidence_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    target_type: Mapped[RagCitationTargetType] = mapped_column(
        Enum(RagCitationTargetType, native_enum=False, length=30), nullable=False
    )
    target_id: Mapped[str] = mapped_column(String(80), nullable=False)
    claim_key: Mapped[str] = mapped_column(String(160), nullable=False)
    claim_kind: Mapped[RagCitationClaimKind] = mapped_column(
        Enum(RagCitationClaimKind, native_enum=False, length=30), nullable=False
    )
    evidence_status: Mapped[RagEvidenceStatus] = mapped_column(
        Enum(RagEvidenceStatus, native_enum=False, length=20), nullable=False
    )
    support_status: Mapped[RagCitationSupportStatus] = mapped_column(
        Enum(RagCitationSupportStatus, native_enum=False, length=30), nullable=False
    )
    authorization_status: Mapped[RagCitationAuthorizationStatus] = mapped_column(
        Enum(RagCitationAuthorizationStatus, native_enum=False, length=20),
        nullable=False,
        default=RagCitationAuthorizationStatus.PENDING,
    )
    release_status: Mapped[RagCitationReleaseStatus] = mapped_column(
        Enum(RagCitationReleaseStatus, native_enum=False, length=20),
        nullable=False,
        default=RagCitationReleaseStatus.NOT_PUBLIC,
    )
    display_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    source_locator: Mapped[str] = mapped_column(String(500), nullable=False)
    public_excerpt: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        comment="Approved source excerpt only; patient text, OCR text, prescription text, and provider raw output are prohibited.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    evidence: Mapped[RagEvidence] = relationship(back_populates="citations")
