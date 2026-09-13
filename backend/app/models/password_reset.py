from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.users import User


class PasswordResetToken(Base):
    """PD-206 결정 3: 비밀번호 재설정 1회용 token입니다. 원문 token은 저장하지 않고
    `token_hash`(SHA-256 hex, 64자)만 저장해, DB가 유출돼도 token이 재사용되지 않습니다."""

    __tablename__ = "password_reset_token"
    __table_args__ = (
        Index("idx_password_reset_token_user_created", "user_id", "created_at"),
        CheckConstraint("length(token_hash) = 64", name="chk_password_reset_token_hash_length"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship()
