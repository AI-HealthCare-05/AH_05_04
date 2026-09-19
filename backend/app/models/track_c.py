"""Track C Safety·Barrier·ActionPlan 저장 계약입니다."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


class SafetyResponseLevel(StrEnum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"
    UNKNOWN = "UNKNOWN"


class SafetyDisposition(StrEnum):
    NORMAL = "NORMAL"
    URGENT_ROUTED = "URGENT_ROUTED"
    EMERGENCY_ROUTED = "EMERGENCY_ROUTED"
    BLOCKED_ACTION = "BLOCKED_ACTION"
    UNKNOWN_RISK = "UNKNOWN_RISK"


class BarrierResponseStatus(StrEnum):
    ANSWERED = "ANSWERED"
    DECLINED = "DECLINED"


class BarrierCode(StrEnum):
    FORGOT = "FORGOT"
    SCHEDULE_OR_TRAVEL = "SCHEDULE_OR_TRAVEL"
    INSTRUCTIONS_UNCLEAR = "INSTRUCTIONS_UNCLEAR"
    NEED_DOUBT = "NEED_DOUBT"
    MEDICATION_CONCERN = "MEDICATION_CONCERN"
    ACCESS_OR_COST = "ACCESS_OR_COST"


class SupportCode(StrEnum):
    REMINDER_SETUP = "REMINDER_SETUP"
    ROUTINE_OR_TRAVEL_PLAN = "ROUTINE_OR_TRAVEL_PLAN"
    INSTRUCTION_REVIEW = "INSTRUCTION_REVIEW"
    PURPOSE_REVIEW = "PURPOSE_REVIEW"
    MEDICATION_CONCERN_GUIDANCE = "MEDICATION_CONCERN_GUIDANCE"
    ACCESS_SUPPORT = "ACCESS_SUPPORT"


class SupportActionPlanStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ActionPlanFollowupResponse(StrEnum):
    HELPED = "HELPED"
    NOT_HELPED = "NOT_HELPED"
    NOT_SURE = "NOT_SURE"


class SafetyAssessment(Base):
    """Append-only evaluated result; checkin_revision is a historical snapshot."""

    __tablename__ = "safety_assessment"
    __table_args__ = (
        CheckConstraint("checkin_lock_marker = 0", name="chk_safety_checkin_lock_marker"),
        UniqueConstraint("medication_checkin_id", "checkin_revision", "revision", name="uq_safety_checkin_revision"),
        UniqueConstraint("id", "medication_checkin_id", "checkin_revision", name="uq_safety_parent_reference"),
        CheckConstraint("checkin_revision > 0 AND revision > 0", name="chk_safety_revisions"),
        CheckConstraint("jsonb_typeof(symptom_codes) = 'array'", name="chk_safety_symptoms_array"),
        CheckConstraint("response_level IN ('ROUTINE', 'URGENT', 'EMERGENCY', 'UNKNOWN')", name="chk_safety_level"),
        CheckConstraint(
            "(response_level = 'ROUTINE' AND safety_disposition = 'NORMAL') OR "
            "(response_level = 'URGENT' AND safety_disposition = 'URGENT_ROUTED') OR "
            "(response_level = 'EMERGENCY' AND safety_disposition = 'EMERGENCY_ROUTED') OR "
            "(response_level = 'UNKNOWN' AND safety_disposition = 'UNKNOWN_RISK') OR "
            "safety_disposition = 'BLOCKED_ACTION'",
            name="chk_safety_disposition",
        ),
    )
    checkin_lock_marker: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    medication_checkin_id: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("medication_checkin.id", name="fk_safety_checkin", ondelete="RESTRICT"), nullable=False
    )
    checkin_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    symptom_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    response_level: Mapped[SafetyResponseLevel] = mapped_column(
        Enum(SafetyResponseLevel, native_enum=False, length=20), nullable=False
    )
    safety_disposition: Mapped[SafetyDisposition] = mapped_column(
        Enum(SafetyDisposition, native_enum=False, length=20), nullable=False
    )
    message_code: Mapped[str] = mapped_column(String(100), nullable=False)
    copy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BarrierResponse(Base):
    """Append-only response bound to the exact Safety observation."""

    __tablename__ = "barrier_response"
    __table_args__ = (
        CheckConstraint("checkin_lock_marker = 0", name="chk_barrier_checkin_lock_marker"),
        ForeignKeyConstraint(
            ["safety_assessment_id", "medication_checkin_id", "checkin_revision"],
            ["safety_assessment.id", "safety_assessment.medication_checkin_id", "safety_assessment.checkin_revision"],
            name="fk_barrier_safety_reference",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("medication_checkin_id", "checkin_revision", "revision", name="uq_barrier_checkin_revision"),
        CheckConstraint("checkin_revision > 0 AND revision > 0", name="chk_barrier_revisions"),
        CheckConstraint(
            "(response_status = 'ANSWERED' AND barrier_code IS NOT NULL) OR (response_status = 'DECLINED' AND barrier_code IS NULL)",
            name="chk_barrier_response",
        ),
        CheckConstraint(
            "barrier_code IN ('FORGOT', 'SCHEDULE_OR_TRAVEL', 'INSTRUCTIONS_UNCLEAR', 'NEED_DOUBT', 'MEDICATION_CONCERN', 'ACCESS_OR_COST')",
            name="chk_barrier_code",
        ),
        CheckConstraint(
            "subreason_code IS NULL OR barrier_code IS NOT NULL",
            name="chk_barrier_subreason_requires_code",
        ),
    )
    checkin_lock_marker: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    medication_checkin_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    checkin_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    safety_assessment_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    response_status: Mapped[BarrierResponseStatus] = mapped_column(
        Enum(BarrierResponseStatus, native_enum=False, length=20), nullable=False
    )
    barrier_code: Mapped[BarrierCode | None] = mapped_column(
        Enum(BarrierCode, native_enum=False, length=30), nullable=True
    )
    # Free-form column, not an enum: the approved subreason vocabulary lives in
    # track_c_personalization.SUBREASONS and is validated before every write.
    subreason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SupportActionPlan(Base):
    """Selected support snapshot; handler-specific schema remains unapproved."""

    __tablename__ = "support_action_plan"
    __table_args__ = (
        Index(
            "uq_action_plan_active_barrier",
            "barrier_response_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("idx_action_plan_barrier", "barrier_response_id"),
        CheckConstraint(
            "support_code IN ('REMINDER_SETUP', 'ROUTINE_OR_TRAVEL_PLAN', 'INSTRUCTION_REVIEW', 'PURPOSE_REVIEW', 'MEDICATION_CONCERN_GUIDANCE', 'ACCESS_SUPPORT')",
            name="chk_action_plan_support",
        ),
        CheckConstraint("jsonb_typeof(action_config_snapshot) = 'object'", name="chk_action_plan_snapshot"),
        CheckConstraint(
            "(status = 'ACTIVE' AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'COMPLETED' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'CANCELLED' AND cancelled_at IS NOT NULL AND completed_at IS NULL)",
            name="chk_action_plan_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    barrier_response_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("barrier_response.id", name="fk_action_plan_barrier", ondelete="RESTRICT"),
        nullable=False,
    )
    support_code: Mapped[SupportCode] = mapped_column(Enum(SupportCode, native_enum=False, length=40), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    copy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    action_config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[SupportActionPlanStatus] = mapped_column(
        Enum(SupportActionPlanStatus, native_enum=False, length=20), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionPlanFollowup(Base):
    """One logical response per plan; corrections retain an audit row."""

    __tablename__ = "action_plan_followup"
    __table_args__ = (
        UniqueConstraint("support_action_plan_id", name="uq_followup_action_plan"),
        CheckConstraint("revision > 0", name="chk_followup_revision"),
        CheckConstraint("response IN ('HELPED', 'NOT_HELPED', 'NOT_SURE')", name="chk_followup_response"),
    )
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    support_action_plan_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("support_action_plan.id", name="fk_followup_action_plan", ondelete="RESTRICT"),
        nullable=False,
    )
    response: Mapped[ActionPlanFollowupResponse] = mapped_column(
        Enum(ActionPlanFollowupResponse, native_enum=False, length=20), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ActionPlanFollowupAudit(Base):
    __tablename__ = "action_plan_followup_audit"
    __table_args__ = (
        UniqueConstraint("followup_id", "to_revision", name="uq_followup_audit_revision"),
        CheckConstraint("from_revision > 0 AND to_revision = from_revision + 1", name="chk_followup_audit_revision"),
        CheckConstraint(
            "from_response IN ('HELPED', 'NOT_HELPED', 'NOT_SURE') AND to_response IN ('HELPED', 'NOT_HELPED', 'NOT_SURE')",
            name="chk_followup_audit_response",
        ),
    )
    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    followup_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("action_plan_followup.id", name="fk_followup_audit_followup", ondelete="RESTRICT"),
        nullable=False,
    )
    from_response: Mapped[ActionPlanFollowupResponse] = mapped_column(
        Enum(ActionPlanFollowupResponse, native_enum=False, length=20), nullable=False
    )
    to_response: Mapped[ActionPlanFollowupResponse] = mapped_column(
        Enum(ActionPlanFollowupResponse, native_enum=False, length=20), nullable=False
    )
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    to_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[UUID] = mapped_column(
        UUIDChar(), ForeignKey("user.id", name="fk_followup_audit_actor", ondelete="RESTRICT"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
