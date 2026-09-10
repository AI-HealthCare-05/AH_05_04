"""Explicit management authorization and append-only provenance receipts (#398)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class SourceManagementPermission(Base):
    __tablename__ = "source_management_permission"

    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Column-level UPDATE is sufficient to lock the row, without permitting self-grants.
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class SourceManagementAudit(Base):
    __tablename__ = "source_management_audit"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_id", name="uq_source_management_actor_request"),
        UniqueConstraint("target_kind", "target_id", "after_revision", name="uq_source_management_target_revision"),
        CheckConstraint("operation IN ('UPDATE','DELETE','GRANT','REVOKE')", name="ck_source_management_operation"),
        CheckConstraint("before_revision >= 0", name="ck_source_management_before_revision"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    operation: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    permission: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    before_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    after_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_provenance: Mapped[dict[str, str | None]] = mapped_column(JSONB, nullable=False)
    after_provenance: Mapped[dict[str, str | None] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
