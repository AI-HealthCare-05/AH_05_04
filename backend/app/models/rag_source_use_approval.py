"""Historical production Source Use Approval persistence for Track F."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUsePurpose


def _sql_in_list(values: type[StrEnum]) -> str:
    return ", ".join(f"'{member.value}'" for member in values)


class RagSourceUseApproval(Base):
    """Immutable Source/Snapshot approval facts, with one-way revocation metadata.

    This table is deliberately separate from ``catalog_source_approval``.  Catalog approval
    remains a Catalog-domain fact; this table binds an explicit production Source Use Purpose
    and canonical runtime environment.
    """

    __tablename__ = "rag_source_use_approval"
    __table_args__ = (
        UniqueConstraint(
            "source_snapshot_id",
            "environment",
            "purpose",
            "approval_version",
            name="uq_rag_source_use_approval_identity",
        ),
        UniqueConstraint(
            "id",
            "source_snapshot_id",
            "source_version",
            name="uq_rag_source_use_approval_id_target",
        ),
        Index("idx_rag_source_use_approval_snapshot", "source_snapshot_id"),
        ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_rag_source_use_approval_snapshot_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"environment IN ({_sql_in_list(RuntimeEnvironmentCode)})",
            name="chk_rag_source_use_approval_environment",
        ),
        CheckConstraint(
            f"purpose IN ({_sql_in_list(SourceUsePurpose)})",
            name="chk_rag_source_use_approval_purpose",
        ),
        CheckConstraint("length(trim(source_code)) > 0", name="chk_rag_source_use_approval_source_code_nonblank"),
        CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_source_use_approval_source_version_nonblank",
        ),
        CheckConstraint(
            "length(trim(approval_version)) > 0",
            name="chk_rag_source_use_approval_version_nonblank",
        ),
        CheckConstraint("length(trim(evidence_ref)) > 0", name="chk_rag_source_use_approval_evidence_nonblank"),
        CheckConstraint(
            "expires_at > valid_from",
            name="chk_rag_source_use_approval_validity_window",
        ),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL AND revoked_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_reason IS NOT NULL)",
            name="chk_rag_source_use_approval_revocation_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    source_code: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(200), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    approval_version: Mapped[str] = mapped_column(String(120), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
