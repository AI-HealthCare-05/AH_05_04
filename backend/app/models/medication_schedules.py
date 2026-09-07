from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class MedicationScheduleEndMode(StrEnum):
    DATE = "DATE"
    OPEN_ENDED = "OPEN_ENDED"


class MedicationScheduleSource(StrEnum):
    PRESCRIPTION_EXACT = "PRESCRIPTION_EXACT"
    USER_CONFIRMED = "USER_CONFIRMED"


class MedicationScheduleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    ENDED = "ENDED"


class MedicationOccurrenceStatus(StrEnum):
    PENDING = "PENDING"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class MedicationSchedule(Base):
    """Track B 일정 모델 초안.

    ``prescription_version_medication_id``는 #169가 제공할 stable id를 담는다.
    #169 병합 전에는 대상 테이블이 없으므로 FK와 약품별 schedule unique 제약을
    의도적으로 만들지 않는다. 두 제약은 #169 산출물에 맞춘 후속 migration에서
    함께 추가한다. ``times`` 관계는 revision 이력을 모두 보존하므로 현재 일정 시간만
    필요한 호출자는 반드시 ``schedule_revision == revision``으로 범위를 제한해야 한다.
    """

    __tablename__ = "medication_schedule"
    __table_args__ = (
        Index("idx_medication_schedule_version_medication", "prescription_version_medication_id"),
        CheckConstraint("revision > 0", name="chk_medication_schedule_revision"),
        CheckConstraint(
            "(end_mode = 'DATE' AND end_local_date IS NOT NULL) "
            "OR (end_mode = 'OPEN_ENDED' AND end_local_date IS NULL)",
            name="chk_medication_schedule_end_mode_date",
        ),
        CheckConstraint(
            "end_local_date IS NULL OR end_local_date >= start_local_date",
            name="chk_medication_schedule_date_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    prescription_version_medication_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    start_local_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_mode: Mapped[MedicationScheduleEndMode] = mapped_column(
        Enum(MedicationScheduleEndMode, native_enum=False, length=20),
        nullable=False,
    )
    end_local_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[MedicationScheduleSource] = mapped_column(
        Enum(MedicationScheduleSource, native_enum=False, length=30),
        nullable=False,
    )
    status: Mapped[MedicationScheduleStatus] = mapped_column(
        Enum(MedicationScheduleStatus, native_enum=False, length=20),
        nullable=False,
        default=MedicationScheduleStatus.ACTIVE,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    times: Mapped[list["MedicationScheduleTime"]] = relationship(
        back_populates="schedule",
        order_by=lambda: (MedicationScheduleTime.schedule_revision, MedicationScheduleTime.local_time),
    )
    occurrences: Mapped[list["MedicationOccurrence"]] = relationship(back_populates="schedule")


class MedicationScheduleTime(Base):
    __tablename__ = "medication_schedule_time"
    __table_args__ = (
        UniqueConstraint(
            "medication_schedule_id",
            "schedule_revision",
            "local_time",
            name="uq_medication_schedule_time_revision_local_time",
        ),
        CheckConstraint("schedule_revision > 0", name="chk_medication_schedule_time_revision"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    medication_schedule_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("medication_schedule.id", ondelete="CASCADE"), nullable=False
    )
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    local_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    schedule: Mapped[MedicationSchedule] = relationship(back_populates="times")
    occurrences: Mapped[list["MedicationOccurrence"]] = relationship(back_populates="schedule_time")


class MedicationOccurrence(Base):
    __tablename__ = "medication_occurrence"
    __table_args__ = (
        UniqueConstraint(
            "medication_schedule_time_id",
            "scheduled_local_date",
            name="uq_medication_occurrence_time_local_date",
        ),
        Index("idx_medication_occurrence_schedule_date", "medication_schedule_id", "scheduled_local_date"),
        Index("idx_medication_occurrence_deadline_status", "confirmation_deadline_at", "status"),
        CheckConstraint("schedule_revision > 0", name="chk_medication_occurrence_schedule_revision"),
        CheckConstraint(
            "confirmation_deadline_at >= scheduled_at",
            name="chk_medication_occurrence_deadline_after_schedule",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    medication_schedule_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("medication_schedule.id", ondelete="RESTRICT"), nullable=False
    )
    medication_schedule_time_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("medication_schedule_time.id", ondelete="RESTRICT"), nullable=False
    )
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_local_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmation_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[MedicationOccurrenceStatus] = mapped_column(
        Enum(MedicationOccurrenceStatus, native_enum=False, length=20),
        nullable=False,
        default=MedicationOccurrenceStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    schedule: Mapped[MedicationSchedule] = relationship(back_populates="occurrences")
    schedule_time: Mapped[MedicationScheduleTime] = relationship(back_populates="occurrences")
