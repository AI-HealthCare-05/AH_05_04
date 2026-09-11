"""Internal PD-417 audit snapshot validation; never a public response DTO."""

from datetime import date, time

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.medication_schedules import (
    MedicationScheduleEndMode,
    MedicationScheduleSource,
    MedicationScheduleStatus,
)


class ScheduleAuditSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_local_date: date
    end_mode: MedicationScheduleEndMode
    end_local_date: date | None
    local_times: list[str]
    status: MedicationScheduleStatus
    source: MedicationScheduleSource

    @field_validator("local_times")
    @classmethod
    def validate_times(cls, values: list[str]) -> list[str]:
        if not values or values != sorted(set(values)):
            raise ValueError("local_times must be nonempty, sorted and unique")
        for value in values:
            parsed = time.fromisoformat(value)
            if parsed.tzinfo is not None or parsed.strftime("%H:%M") != value:
                raise ValueError("local_times must use HH:mm")
        return values

    @model_validator(mode="after")
    def validate_dates(self) -> "ScheduleAuditSnapshot":
        if (self.end_mode == MedicationScheduleEndMode.DATE) != (self.end_local_date is not None):
            raise ValueError("end mode and date disagree")
        if self.end_local_date is not None and self.end_local_date < self.start_local_date:
            raise ValueError("end date precedes start date")
        return self
