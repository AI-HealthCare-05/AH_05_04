from datetime import UTC
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ApiError, ErrorDetail
from app.models.medication_schedules import MedicationCheckinStatus


class PutMedicationCheckinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["TAKEN", "NOT_TAKEN"]
    taken_at: AwareDatetime | None = None
    expected_revision: int = Field(ge=0, strict=True)

    @field_validator("status", mode="before")
    @classmethod
    def reject_scheduler_status(cls, value: object) -> object:
        if value == "UNCONFIRMED":
            raise ApiError(
                status_code=422,
                code="CHECKIN_STATUS_NOT_USER_SETTABLE",
                message="미확인 상태는 응답 기한이 지난 경우에만 자동 생성됩니다.",
                details=[ErrorDetail(field="status", reason="NOT_USER_SETTABLE")],
            )
        return value

    @model_validator(mode="after")
    def validate_taken_at(self) -> Self:
        if self.taken_at is not None:
            if self.status != "TAKEN":
                raise ValueError("taken_at is only allowed for TAKEN")
            self.taken_at = self.taken_at.astimezone(UTC)
        return self


class MedicationCheckinData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checkin_id: UUID
    occurrence_id: UUID
    status: MedicationCheckinStatus
    taken_at: AwareDatetime | None
    revision: int
    corrected: bool


class MedicationCheckinResponse(BaseModel):
    data: MedicationCheckinData
