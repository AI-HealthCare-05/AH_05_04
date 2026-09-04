from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class Gender(StrEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"


class AccountStatus(StrEnum):
    """PD-206 결정 1: 계정 생명주기 상태입니다. `ACTIVE`가 아니게 되는 시점에 `is_active`도
    함께 `false`로 전환하되, 두 컬럼은 항상 같은 상태 전이 지점(서비스 계층 헬퍼)에서만
    함께 갱신해야 합니다 — `is_active`를 단독으로 되돌리는 코드 경로를 두지 않습니다."""

    ACTIVE = "ACTIVE"
    WITHDRAWAL_REQUESTED = "WITHDRAWAL_REQUESTED"
    WITHDRAWN = "WITHDRAWN"


class User(Base):
    __tablename__ = "user"

    id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
        index=True,
    )
    hashed_password: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    gender: Mapped[Gender | None] = mapped_column(
        Enum(
            Gender,
            native_enum=False,
            length=10,
        ),
        nullable=True,
    )
    birthday: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    phone_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        unique=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    account_status: Mapped[AccountStatus] = mapped_column(
        Enum(
            AccountStatus,
            native_enum=False,
            length=25,
        ),
        nullable=False,
        default=AccountStatus.ACTIVE,
    )
    withdrawal_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # PD-206 결정 1: 세션 무효화 판정에 쓰는 단조 증가 카운터입니다. 로그아웃·비밀번호
    # 재설정·회원탈퇴마다 원자적으로 +1하고, 토큰의 token_version 클레임과 정확히
    # 일치하지 않으면(크든 작든) 만료 전이라도 무효로 취급합니다.
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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
