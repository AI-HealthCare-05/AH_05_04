from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class FeedbackFields:
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GuideFeedback(FeedbackFields, Base):
    __tablename__ = "guide_feedback"
    __table_args__ = (
        UniqueConstraint("guide_id", name="uq_guide_feedback_target"),
        CheckConstraint("rating IN ('POSITIVE', 'NEGATIVE')", name="chk_guide_feedback_rating"),
        Index("idx_guide_feedback_created", "created_at"),
        Index("idx_guide_feedback_review", "rating", "updated_at", "id"),
    )
    guide_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("guide.id", ondelete="CASCADE"), nullable=False)


class ChatMessageFeedback(FeedbackFields, Base):
    __tablename__ = "chat_message_feedback"
    __table_args__ = (
        UniqueConstraint("chat_message_id", name="uq_chat_message_feedback_target"),
        CheckConstraint("rating IN ('POSITIVE', 'NEGATIVE')", name="chk_chat_message_feedback_rating"),
        Index("idx_chat_message_feedback_created", "created_at"),
        Index("idx_chat_message_feedback_review", "rating", "updated_at", "id"),
    )
    chat_message_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("chat_message.id", ondelete="CASCADE"), nullable=False
    )
