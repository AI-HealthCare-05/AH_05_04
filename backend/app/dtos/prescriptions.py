from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UpdateExtractedFieldRequest(BaseModel):
    confirmed_value: str = Field(min_length=1)


class MedicationData(BaseModel):
    medication_name: str
    dose_value: float | None = None
    dose_unit: str | None = None
    frequency_per_day: int | None = None
    timing_text: str | None = None
    duration_days: int | None = None
    display_order: int


class PrescriptionData(BaseModel):
    prescription_id: UUID
    document_id: UUID
    prescribed_date: date
    confirmed_at: datetime
    medications: list[MedicationData] = Field(default_factory=list)


class PrescriptionResponse(BaseModel):
    data: PrescriptionData
