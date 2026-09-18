"""Declarative SQLAlchemy model for Track F Evidence Assessment Authority (#712).

테이블 `rag_evidence_authority`는 Production Evidence Gate 평가를 통과한
선택 청크(selected hit)에 대해 발급된 불변 Assessment·Eligibility Authority를 영속화합니다.

불변성 및 거버넌스 규칙:
- 논리적 식별자: UNIQUE(retrieval_run_id, knowledge_chunk_id)
- 고유 아티팩트 참조: UNIQUE(assessment_artifact_code, assessment_artifact_version, assessment_artifact_sha256)
- Foreign Key: retrieval_hit, rag_source_snapshot, rag_source_snapshot_member에 대해 RESTRICT delete
- 유효성 구간 제약: assessment_valid_from < assessment_valid_until
- 하프오픈 구간 제약: evaluated_at >= assessment_valid_from AND evaluated_at < assessment_valid_until
- 트리거/RLS/SP 금지 (AGENTS.md 준수)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

__all__ = ["RagEvidenceAuthority"]


class RagEvidenceAuthority(Base):
    """Production Evidence Gate 통과 청크의 불변 평가 권위 레코드."""

    __tablename__ = "rag_evidence_authority"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_rag_evidence_authority"),
        UniqueConstraint(
            "retrieval_run_id",
            "knowledge_chunk_id",
            name="uq_rag_evidence_authority_run_chunk",
        ),
        UniqueConstraint(
            "assessment_artifact_code",
            "assessment_artifact_version",
            "assessment_artifact_sha256",
            name="uq_rag_evidence_authority_assessment_ref",
        ),
        ForeignKeyConstraint(
            ["retrieval_run_id", "knowledge_chunk_id"],
            ["retrieval_hit.retrieval_run_id", "retrieval_hit.knowledge_chunk_id"],
            name="fk_rag_evidence_authority_hit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["rag_source_snapshot.id"],
            name="fk_rag_evidence_authority_snapshot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_member_id"],
            ["rag_source_snapshot_member.id"],
            name="fk_rag_evidence_authority_snapshot_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "assessment_valid_from < assessment_valid_until",
            name="chk_rag_evidence_authority_validity_window",
        ),
        CheckConstraint(
            "evaluated_at >= assessment_valid_from AND evaluated_at < assessment_valid_until",
            name="chk_rag_evidence_authority_evaluated_at_in_window",
        ),
        CheckConstraint(
            "length(trim(source_code)) > 0",
            name="chk_rag_evidence_authority_source_code_nonblank",
        ),
        CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_evidence_authority_source_version_nonblank",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_content_sha256",
        ),
        CheckConstraint(
            "eligibility_receipt_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_eligibility_sha256",
        ),
        CheckConstraint(
            "assessment_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_assessment_sha256",
        ),
        CheckConstraint(
            "verifier_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_verifier_sha256",
        ),
        CheckConstraint(
            "validity_policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="chk_rag_evidence_authority_validity_policy_sha256",
        ),
        Index(
            "idx_rag_evidence_authority_assessment_ref",
            "assessment_artifact_code",
            "assessment_artifact_version",
            "assessment_artifact_sha256",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    retrieval_run_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    knowledge_chunk_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_snapshot_member_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_code: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    eligibility_receipt_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    eligibility_receipt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    eligibility_receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    assessment_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    assessment_artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    assessment_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    verifier_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    verifier_artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    verifier_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    validity_policy_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    validity_policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    validity_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assessment_valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assessment_valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
