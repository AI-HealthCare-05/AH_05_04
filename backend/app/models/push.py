from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class PushSubscription(Base):
    __tablename__ = "push_subscription"
    __table_args__ = (
        UniqueConstraint("endpoint_hmac", name="uq_push_subscription_endpoint"),
        CheckConstraint("token_version >= 0", name="chk_push_subscription_token_version"),
        CheckConstraint(
            "(revoked_at IS NULL AND ciphertext IS NOT NULL) OR (revoked_at IS NOT NULL AND ciphertext IS NULL)",
            name="chk_push_subscription_revocation",
        ),
        Index("idx_push_subscription_profile", "profile_id"),
    )
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("profile.id", ondelete="CASCADE"), nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False, default=uuid4)
    endpoint_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PushDelivery(Base):
    __tablename__ = "push_delivery"
    __table_args__ = (
        UniqueConstraint("notification_id", "subscription_id", "generation", name="uq_push_delivery_generation"),
        CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'ACCEPTED', 'FAILED', 'UNKNOWN', 'CANCELLED')",
            name="chk_push_delivery_status",
        ),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="chk_push_delivery_attempt"),
        CheckConstraint(
            "(status = 'SENDING' AND claim_token IS NOT NULL AND claim_expires_at IS NOT NULL) OR "
            "(status != 'SENDING' AND claim_token IS NULL AND claim_expires_at IS NULL)",
            name="chk_push_delivery_claim",
        ),
        CheckConstraint(
            "(status = 'ACCEPTED' AND accepted_at IS NOT NULL) OR (status != 'ACCEPTED' AND accepted_at IS NULL)",
            name="chk_push_delivery_accepted",
        ),
        Index("idx_push_delivery_due", "status", "next_attempt_at"),
    )
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    notification_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("notification_record.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("push_subscription.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="PENDING", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claim_token: Mapped[UUID | None] = mapped_column(UUIDChar())
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(30))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
