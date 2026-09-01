from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.users import User


class ProfileType(StrEnum):
    SELF = "SELF"


class Profile(Base):
    __tablename__ = "profile"
    __table_args__ = (
        UniqueConstraint("user_id", "profile_type", name="uq_profile_user_type"),
        CheckConstraint("profile_type = 'SELF'", name="chk_profile_type_mvp_self"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    profile_type: Mapped[ProfileType] = mapped_column(
        Enum(ProfileType, native_enum=False, length=30),
        nullable=False,
        default=ProfileType.SELF,
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
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
