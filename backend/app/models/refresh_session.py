from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.users import User


class RefreshSession(Base):
    """#206 리뷰(권가빈): refresh token rotation의 재사용 탐지를 `User` 컬럼 하나로
    두면 같은 사용자의 여러 기기 로그인이 서로를 재사용 공격으로 오판한다. 로그인마다
    독립된 row를 만들어, rotation의 CAS를 `user_id`가 아니라 이 row(`id`=토큰의
    `session_id` claim)로 스코프한다."""

    __tablename__ = "refresh_session"

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    active_jti: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship()

    __table_args__ = (Index("idx_refresh_session_user", "user_id"),)
