from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class EmailVerificationPurpose(StrEnum):
    SIGNUP = "SIGNUP"


class EmailVerificationToken(Base):
    """회원가입 전 이메일 소유 확인용 1회성 token입니다.

    아직 User row가 없을 수 있는 시점이므로 user_id FK를 두지 않고 이메일 문자열과
    purpose를 기준으로 관리합니다. 원문 token은 저장하지 않습니다.
    """

    __tablename__ = "email_verification_token"
    __table_args__ = (
        Index("idx_email_verification_token_email_purpose_created", "email", "purpose", "created_at"),
        CheckConstraint("length(token_hash) = 64", name="chk_email_verification_token_hash_length"),
        CheckConstraint("length(trim(email)) > 0", name="chk_email_verification_token_email_nonblank"),
        CheckConstraint("purpose IN ('SIGNUP')", name="chk_email_verification_token_purpose"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[EmailVerificationPurpose] = mapped_column(
        Enum(EmailVerificationPurpose, native_enum=False, length=30),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
