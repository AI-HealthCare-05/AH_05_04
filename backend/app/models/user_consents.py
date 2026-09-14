from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.users import User


class ConsentPurpose(StrEnum):
    OCR = "OCR"
    GUIDE = "GUIDE"
    CHAT = "CHAT"
    NOTIFICATION = "NOTIFICATION"


class ConsentStatus(StrEnum):
    GRANTED = "GRANTED"
    WITHDRAWN = "WITHDRAWN"


class UserConsent(Base):
    """PD-207 목적별 최신 동의 상태 row입니다.

    과거 동의 이력은 이 테이블에 누적하지 않습니다. 사용자별·목적별 current row만
    보존하며, row가 없으면 미동의로 판정하는 계약은 Gate 구현 PR에서 사용합니다.
    """

    __tablename__ = "user_consent"
    __table_args__ = (
        UniqueConstraint("user_id", "purpose", name="uq_user_consent_user_purpose"),
        CheckConstraint(
            "purpose IN ('OCR', 'GUIDE', 'CHAT', 'NOTIFICATION')",
            name="chk_user_consent_purpose",
        ),
        CheckConstraint(
            "status IN ('GRANTED', 'WITHDRAWN')",
            name="chk_user_consent_status",
        ),
        CheckConstraint(
            "length(policy_version) > 0",
            name="chk_user_consent_policy_version_non_empty",
        ),
        CheckConstraint(
            "(status = 'GRANTED' AND granted_at IS NOT NULL AND withdrawn_at IS NULL) OR "
            "(status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL)",
            name="chk_user_consent_status_timestamps",
        ),
        Index("idx_user_consent_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("user.id", name="fk_user_consent_user"),
        nullable=False,
    )
    purpose: Mapped[ConsentPurpose] = mapped_column(
        Enum(ConsentPurpose, native_enum=False, length=20),
        nullable=False,
    )
    status: Mapped[ConsentStatus] = mapped_column(
        Enum(ConsentStatus, native_enum=False, length=20),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
