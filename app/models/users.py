from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.medical_document import MedicalDocument


class Gender(StrEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"


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
    gender: Mapped[Gender] = mapped_column(
        Enum(
            Gender,
            native_enum=False,
            length=10,
        ),
        nullable=False,
    )
    birthday: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    phone_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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

    medical_documents: Mapped[list["MedicalDocument"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
