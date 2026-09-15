from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ApiError, ErrorDetail
from app.models.track_c import (
    BarrierCode,
    BarrierResponseStatus,
    SafetyDisposition,
    SafetyResponseLevel,
)


class CreateSafetyAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    medication_checkin_id: UUID
    checkin_revision: int = Field(gt=0, strict=True)
    symptom_codes: list[str]
    expected_revision: int = Field(ge=0, strict=True)

    @field_validator("symptom_codes", mode="before")
    @classmethod
    def reject_free_text_symptoms(cls, value: object) -> object:
        if isinstance(value, str):
            raise ApiError(
                status_code=422,
                code="FREE_TEXT_SYMPTOM_NOT_SUPPORTED",
                message="증상은 승인된 구조화 코드 목록으로만 제출할 수 있습니다.",
                details=[ErrorDetail(field="symptom_codes", reason="STRUCTURED_CODES_REQUIRED")],
            )
        return value


class SafetyAssessmentData(BaseModel):
    assessment_id: UUID
    medication_checkin_id: UUID
    checkin_revision: int
    response_level: SafetyResponseLevel
    safety_disposition: SafetyDisposition
    message_code: str
    copy_version: str
    source_version: str
    revision: int


class SafetyAssessmentResponse(BaseModel):
    data: SafetyAssessmentData


class PutBarrierResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_status: Literal["ANSWERED", "DECLINED"]
    barrier_code: BarrierCode | None = None
    checkin_revision: int = Field(gt=0, strict=True)
    expected_revision: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def validate_status_and_code(self) -> Self:
        if self.response_status == "ANSWERED" and self.barrier_code is None:
            raise ValueError("barrier_code is required for ANSWERED")
        if self.response_status == "DECLINED" and self.barrier_code is not None:
            raise ValueError("barrier_code must be null for DECLINED")
        return self


class BarrierResponseData(BaseModel):
    barrier_response_id: UUID
    medication_checkin_id: UUID
    checkin_revision: int
    safety_assessment_id: UUID
    response_status: BarrierResponseStatus
    barrier_code: BarrierCode | None
    revision: int


class BarrierResponseEnvelope(BaseModel):
    data: BarrierResponseData
