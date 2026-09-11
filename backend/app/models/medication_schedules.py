from datetime import date, datetime, time
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
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


class MedicationCheckinStatus(StrEnum):
    TAKEN = "TAKEN"
    NOT_TAKEN = "NOT_TAKEN"
    UNCONFIRMED = "UNCONFIRMED"


class MedicationSchedule(Base):
    """Track B 복약 일정.

    ``prescription_version_medication_id``는 #169의 불변 약품 snapshot을 참조한다.
    ``times`` 관계는 revision 이력을 모두 보존하므로 현재 일정 시간만 필요한 호출자는
    반드시 ``schedule_revision == revision``으로 범위를 제한해야 한다.
    """

    __tablename__ = "medication_schedule"
    __table_args__ = (
        UniqueConstraint(
            "prescription_version_medication_id",
            name="uq_medication_schedule_version_medication",
        ),
        CheckConstraint("revision > 0", name="chk_medication_schedule_revision"),
        CheckConstraint(
            "end_mode IN ('DATE', 'OPEN_ENDED')",
            name="chk_medication_schedule_end_mode",
        ),
        CheckConstraint(
            "source IN ('PRESCRIPTION_EXACT', 'USER_CONFIRMED')",
            name="chk_medication_schedule_source",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'CANCELLED', 'ENDED')",
            name="chk_medication_schedule_status",
        ),
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
    prescription_version_medication_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "prescription_version_medication.id",
            name="fk_medication_schedule_version_medication",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
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
        UUIDChar(),
        ForeignKey(
            "medication_schedule.id",
            name="fk_medication_schedule_time_schedule",
            ondelete="CASCADE",
        ),
        nullable=False,
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
            "status IN ('PENDING', 'CANCELLED', 'CLOSED')",
            name="chk_medication_occurrence_status",
        ),
        CheckConstraint(
            "confirmation_deadline_at >= scheduled_at",
            name="chk_medication_occurrence_deadline_after_schedule",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    medication_schedule_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "medication_schedule.id",
            name="fk_medication_occurrence_schedule",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    medication_schedule_time_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "medication_schedule_time.id",
            name="fk_medication_occurrence_schedule_time",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    schedule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_local_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    checkin: Mapped["MedicationCheckin | None"] = relationship(back_populates="occurrence", uselist=False)


class MedicationCheckin(Base):
    """Occurrence별 단 하나의 현재 Check-in 결과."""

    __tablename__ = "medication_checkin"
    __table_args__ = (
        UniqueConstraint("occurrence_id", name="uq_medication_checkin_occurrence_id"),
        CheckConstraint("status IN ('TAKEN', 'NOT_TAKEN', 'UNCONFIRMED')", name="chk_medication_checkin_status"),
        CheckConstraint("revision > 0", name="chk_medication_checkin_revision"),
        CheckConstraint(
            "status = 'TAKEN' OR taken_at IS NULL",
            name="chk_medication_checkin_taken_at_status",
        ),
        Index("idx_medication_checkin_status_updated", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    occurrence_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "medication_occurrence.id",
            name="fk_medication_checkin_occurrence",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[MedicationCheckinStatus] = mapped_column(
        Enum(MedicationCheckinStatus, native_enum=False, length=20),
        nullable=False,
    )
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    occurrence: Mapped[MedicationOccurrence] = relationship(back_populates="checkin")
    audits: Mapped[list["CheckinAudit"]] = relationship(
        back_populates="checkin",
        order_by=lambda: CheckinAudit.to_revision,
    )


class CheckinAudit(Base):
    """Check-in 정정마다 추가되는 불변 감사 이력."""

    __tablename__ = "checkin_audit"
    __table_args__ = (
        UniqueConstraint("checkin_id", "to_revision", name="uq_checkin_audit_checkin_to_revision"),
        CheckConstraint(
            "from_status IN ('TAKEN', 'NOT_TAKEN', 'UNCONFIRMED')",
            name="chk_checkin_audit_from_status",
        ),
        CheckConstraint(
            "to_status IN ('TAKEN', 'NOT_TAKEN', 'UNCONFIRMED')",
            name="chk_checkin_audit_to_status",
        ),
        CheckConstraint("from_revision > 0", name="chk_checkin_audit_from_revision"),
        CheckConstraint("to_revision = from_revision + 1", name="chk_checkin_audit_revision_step"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    checkin_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("medication_checkin.id", name="fk_checkin_audit_checkin", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[MedicationCheckinStatus] = mapped_column(
        Enum(MedicationCheckinStatus, native_enum=False, length=20),
        nullable=False,
    )
    to_status: Mapped[MedicationCheckinStatus] = mapped_column(
        Enum(MedicationCheckinStatus, native_enum=False, length=20),
        nullable=False,
    )
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    to_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("user.id", name="fk_checkin_audit_changed_by", ondelete="RESTRICT"),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    checkin: Mapped[MedicationCheckin] = relationship(back_populates="audits")


class MedicationScheduleAudit(Base):
    """PD-417 일정 변경 이력. Repository는 append만 제공한다."""

    __tablename__ = "medication_schedule_audit"
    __table_args__ = (
        UniqueConstraint("medication_schedule_id", "to_revision", name="uq_schedule_audit_to_revision"),
        CheckConstraint("from_revision >= 0", name="chk_schedule_audit_from_revision"),
        CheckConstraint("to_revision = from_revision + 1", name="chk_schedule_audit_revision_step"),
        CheckConstraint(
            "(from_revision = 0 AND before_snapshot IS NULL) OR (from_revision > 0 AND before_snapshot IS NOT NULL)",
            name="chk_schedule_audit_before_snapshot",
        ),
        CheckConstraint(
            "(change_source = 'USER' AND changed_by IS NOT NULL) OR "
            "(change_source = 'SCHEDULER' AND changed_by IS NULL)",
            name="chk_schedule_audit_actor",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    medication_schedule_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("medication_schedule.id", ondelete="RESTRICT"), nullable=False
    )
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    to_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    after_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[UUID | None] = mapped_column(
        UUIDChar(), ForeignKey("user.id", ondelete="RESTRICT"), nullable=True
    )
    change_source: Mapped[str] = mapped_column(Enum("USER", "SCHEDULER", native_enum=False, length=20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
