from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class NotificationKind(StrEnum):
    SCHEDULED = "SCHEDULED"
    REMINDER = "REMINDER"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class NotificationRecord(Base):
    __tablename__ = "notification_record"
    __table_args__ = (
        UniqueConstraint("occurrence_id", "kind", name="uq_notification_occurrence_kind"),
        CheckConstraint("kind IN ('SCHEDULED', 'REMINDER')", name="chk_notification_kind"),
        CheckConstraint(
            "(status = 'PENDING' AND attempt = 0 AND cancelled_at IS NULL AND delivered_at IS NULL AND read_at IS NULL) OR "
            "(status = 'DELIVERED' AND attempt = 1 AND cancelled_at IS NULL AND delivered_at IS NOT NULL) OR "
            "(status = 'CANCELLED' AND attempt = 0 AND cancelled_at IS NOT NULL AND delivered_at IS NULL AND read_at IS NULL)",
            name="chk_notification_state",
        ),
        CheckConstraint("read_at IS NULL OR read_at >= delivered_at", name="chk_notification_read_at"),
        Index("idx_notification_status_scheduled", "status", "scheduled_at"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    occurrence_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("medication_occurrence.id", ondelete="CASCADE", name="fk_notification_occurrence"),
        nullable=False,
    )
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind, native_enum=False, length=20), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, native_enum=False, length=20),
        nullable=False,
        default=NotificationStatus.PENDING,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
