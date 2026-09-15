from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class LifestyleTimes(Base):
    __tablename__ = "lifestyle_times"
    __table_args__ = (
        CheckConstraint("revision > 0", name="chk_lifestyle_times_revision_positive"),
        CheckConstraint("jsonb_typeof(days) = 'array'", name="chk_lifestyle_times_days_array"),
    )

    profile_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("profile.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    days: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
