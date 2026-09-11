from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.dtos.medication_checkins import MedicationCheckinData
from app.models.medication_schedules import (
    MedicationOccurrenceStatus,
    MedicationScheduleEndMode,
    MedicationScheduleStatus,
)

LocalTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
SetupReason = Literal[
    "UNSUPPORTED_SCHEDULE_PATTERN",
    "MISSING_START_DATE",
    "MISSING_EXACT_TIME",
    "MISSING_DURATION_DECISION",
    "USER_CONFIRMATION_REQUIRED",
]
ItemStatus = Literal["READY", "SETUP_REQUIRED", "INACTIVE"]


class PutMedicationScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_local_date: date
    end_mode: MedicationScheduleEndMode
    end_local_date: date | None = None
    local_times: list[LocalTime] = Field(min_length=1)
    expected_revision: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        if len(self.local_times) != len(set(self.local_times)):
            raise ValueError("duplicate local times")
        if (self.end_mode == MedicationScheduleEndMode.DATE) != (self.end_local_date is not None):
            raise ValueError("end mode and date disagree")
        if self.end_local_date is not None and self.end_local_date < self.start_local_date:
            raise ValueError("end precedes start")
        self.local_times = sorted(self.local_times)
        return self


class CancelMedicationScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["CANCELLED"]
    expected_revision: int = Field(ge=0, strict=True)


class MedicationScheduleData(BaseModel):
    schedule_id: UUID
    prescription_version_medication_id: UUID
    revision: int
    status: MedicationScheduleStatus
    start_local_date: date
    end_mode: MedicationScheduleEndMode
    end_local_date: date | None
    local_times: list[LocalTime]


class MedicationScheduleResponse(BaseModel):
    data: MedicationScheduleData


class MedicationScheduleItem(BaseModel):
    prescription_version_medication_id: UUID
    schedule_item_status: ItemStatus
    schedule_id: UUID | None
    revision: int | None
    setup_reason: SetupReason | None


class MedicationOccurrenceData(BaseModel):
    occurrence_id: UUID
    prescription_version_id: UUID
    prescription_version_medication_id: UUID
    scheduled_local_date: date
    scheduled_at: AwareDatetime
    confirmation_deadline_at: AwareDatetime
    status: MedicationOccurrenceStatus
    checkin: MedicationCheckinData | None


class MedicationDayData(BaseModel):
    schedule_status: Literal["READY", "PARTIAL", "SETUP_REQUIRED", "INACTIVE", "NO_ACTIVE_PRESCRIPTION"]
    schedule_items: list[MedicationScheduleItem]
    occurrences: list[MedicationOccurrenceData]


class MedicationDayResponse(BaseModel):
    data: MedicationDayData
