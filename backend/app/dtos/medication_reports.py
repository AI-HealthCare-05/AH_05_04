from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.dtos.medication_checkins import MedicationCheckinData
from app.dtos.medication_schedules import MedicationOccurrenceData
from app.models.track_c import BarrierCode, SupportCode

# The clinic view is the only surface approved to show why a dose was missed.
MedicationReportView = Literal["CLINIC"]


class MedicationReportCounts(BaseModel):
    taken_count: int = Field(default=0, ge=0)
    not_taken_count: int = Field(default=0, ge=0)
    unconfirmed_count: int = Field(default=0, ge=0)
    pending_count: int = Field(default=0, ge=0)
    cancelled_count: int = Field(default=0, ge=0)


class MedicationReportRate(BaseModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    percentage: float | None = Field(ge=0, le=100)


MedicationReportTimeSlot = Literal["BREAKFAST", "LUNCH", "DINNER", "BEDTIME"]


class MedicationReportCheckin(MedicationCheckinData):
    updated_at: AwareDatetime


class MedicationReportRecord(MedicationOccurrenceData):
    medication_name: str
    strength_text: str | None
    time_slot: MedicationReportTimeSlot
    checkin: MedicationReportCheckin | None
    updated_at: AwareDatetime


class ClinicBarrierEntry(BaseModel):
    occurrence_id: UUID
    scheduled_local_date: date
    medication_name: str
    barrier_code: BarrierCode
    subreason_code: str | None = None


class ClinicConsultationQuestion(BaseModel):
    question_id: str
    text: str
    support_code: SupportCode
    medication_name: str
    last_selected_date: date


class ClinicSections(BaseModel):
    """Only populated for view=CLINIC; absent everywhere else by construction."""

    barriers: list[ClinicBarrierEntry]
    consultation_questions: list[ClinicConsultationQuestion]


class MedicationReportData(BaseModel):
    period_days: Literal[7, 30]
    start_date: date
    end_date: date
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    as_of: AwareDatetime
    counts: MedicationReportCounts
    overdue_pending_count: int = Field(ge=0)
    adherence_rate: MedicationReportRate
    confirmation_rate: MedicationReportRate
    records: list[MedicationReportRecord]
    clinic: ClinicSections | None = None


class MedicationReportResponse(BaseModel):
    data: MedicationReportData
