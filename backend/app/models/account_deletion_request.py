from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.users import User


class AccountDeletionRequestStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AccountDeletionRequest(Base):
    """account-lifecycle-v1 5절(#206): 회원탈퇴 요청 이후 개인정보·건강정보 삭제·보존
    처리의 감사 기준 row입니다. 로그인 가능 여부는 `User.account_status`가 판단하고,
    이 테이블은 삭제·보존 처리의 대기·진행·완료·실패 상태와 재처리 근거만 관리합니다."""

    __tablename__ = "account_deletion_request"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED')",
            name="chk_account_deletion_request_status",
        ),
        CheckConstraint("retry_count >= 0", name="chk_account_deletion_request_retry_count"),
        # 한 사용자에게 terminal 전 요청(PENDING/IN_PROGRESS/FAILED)이 동시에 여러 개 생기는 것을
        # DB 레벨에서 막는다. COMPLETED는 이 partial index 대상이 아니라 과거 이력으로 남는다.
        Index(
            "uq_account_deletion_request_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'IN_PROGRESS', 'FAILED')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    status: Mapped[AccountDeletionRequestStatus] = mapped_column(
        Enum(AccountDeletionRequestStatus, native_enum=False, length=20),
        nullable=False,
        default=AccountDeletionRequestStatus.PENDING,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
