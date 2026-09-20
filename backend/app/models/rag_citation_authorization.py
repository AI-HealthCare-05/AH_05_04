"""Historical Citation Authorization Decisions and Receipts (#869)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

_SHA_CHECK = " ~ '^[0-9a-f]{64}$'"


class RagCitationAuthorizationSourceDecision(Base):
    __tablename__ = "rag_citation_authorization_source_decision"
    __table_args__ = (
        UniqueConstraint(
            "artifact_code", "artifact_version", "artifact_content_sha256", name="uq_rag_citation_auth_source_artifact"
        ),
        ForeignKeyConstraint(
            ["bundle_id", "bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "artifact_code = 'citation_authorization_source_decision'", name="chk_rag_citation_auth_source_code"
        ),
        CheckConstraint("artifact_content_sha256" + _SHA_CHECK, name="chk_rag_citation_auth_source_hash"),
        CheckConstraint("request_sha256" + _SHA_CHECK, name="chk_rag_citation_auth_source_request_hash"),
        CheckConstraint("purpose = 'PATIENT_CITATION'", name="chk_rag_citation_auth_source_purpose"),
        CheckConstraint("actual_decision_outcome IN ('PASS', 'FAIL')", name="chk_rag_citation_auth_source_outcome"),
        Index("idx_rag_citation_auth_source_request", "request_sha256"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_guard_decision_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_request_guard_runtime_binding.request_guard_decision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    origin_guard_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    origin_guard_artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    origin_guard_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    request_operation_code: Mapped[str] = mapped_column(String(100), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    bundle_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot.id", ondelete="RESTRICT"), nullable=False
    )
    source_use_approval_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_use_approval.id", ondelete="RESTRICT"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    approval_version: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_verification_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actual_decision_outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagCitationAuthorizationMemberDecision(Base):
    __tablename__ = "rag_citation_authorization_member_decision"
    __table_args__ = (
        UniqueConstraint(
            "artifact_code", "artifact_version", "artifact_content_sha256", name="uq_rag_citation_auth_member_artifact"
        ),
        CheckConstraint(
            "artifact_code = 'citation_authorization_member_decision'", name="chk_rag_citation_auth_member_code"
        ),
        CheckConstraint("artifact_content_sha256" + _SHA_CHECK, name="chk_rag_citation_auth_member_hash"),
        CheckConstraint("actual_decision_outcome IN ('PASS', 'FAIL')", name="chk_rag_citation_auth_member_outcome"),
        Index("idx_rag_citation_auth_member_request", "request_sha256"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_decision_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_citation_authorization_source_decision.id", ondelete="RESTRICT"), nullable=False
    )
    artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_decision_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_snapshot_member_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_source_snapshot_member.id", ondelete="RESTRICT"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    member_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    endpoint_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    operation_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    artifact_member_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    artifact_member_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evaluation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    endpoint_lifecycle_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    endpoint_runtime_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    endpoint_acquisition_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    operation_runtime_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    operation_acquisition_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    current_member_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    snapshot_endpoint_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    snapshot_operation_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    current_endpoint_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    current_operation_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    current_ingestion_artifact_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    artifact_fk_exists: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    actual_decision_outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagCitationAuthorizationReceipt(Base):
    __tablename__ = "rag_citation_authorization_receipt"
    __table_args__ = (
        UniqueConstraint("request_sha256", name="uq_rag_citation_auth_receipt_request"),
        UniqueConstraint(
            "artifact_code", "artifact_version", "artifact_content_sha256", name="uq_rag_citation_auth_receipt_artifact"
        ),
        CheckConstraint("artifact_code = 'citation_authorization_receipt'", name="chk_rag_citation_auth_receipt_code"),
        CheckConstraint("origin_decision = 'PASS'", name="chk_rag_citation_auth_receipt_origin"),
        CheckConstraint("operation = 'CITATION_AUTHORIZATION'", name="chk_rag_citation_auth_receipt_operation"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    artifact_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_guard_artifact_code: Mapped[str] = mapped_column(String(100), nullable=False)
    origin_guard_artifact_version: Mapped[str] = mapped_column(String(50), nullable=False)
    origin_guard_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_decision: Mapped[str] = mapped_column(String(10), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    bundle_id: Mapped[str] = mapped_column(String(36), nullable=False)
    bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_scope_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    scope_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validated_selection_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagCitationAuthorizationReceiptSelection(Base):
    __tablename__ = "rag_citation_authorization_receipt_selection"
    __table_args__ = (
        UniqueConstraint("receipt_id", "selection_order", name="uq_rag_citation_auth_receipt_selection_order"),
        CheckConstraint("selection_order >= 0", name="chk_rag_citation_auth_selection_order"),
        CheckConstraint("selected_for_operation", name="chk_rag_citation_auth_selection_selected"),
        CheckConstraint("purpose = 'PATIENT_CITATION'", name="chk_rag_citation_auth_selection_purpose"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    receipt_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_citation_authorization_receipt.id", ondelete="RESTRICT"), nullable=False
    )
    selection_order: Mapped[int] = mapped_column(Integer, nullable=False)
    source_decision_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_citation_authorization_source_decision.id", ondelete="RESTRICT"), nullable=False
    )
    member_decision_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("rag_citation_authorization_member_decision.id", ondelete="RESTRICT"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    member_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    endpoint_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    operation_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    artifact_member_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    artifact_member_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    selected_for_operation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    source_decision: Mapped[str] = mapped_column(String(10), nullable=False)
    member_decision: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
