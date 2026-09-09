from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OcrJobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExecuteOcrRequest(BaseModel):
    force_reprocess: bool = False


class CreateManualMedicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    medication_name: str = Field(min_length=1, max_length=255)
    medication_strength: str | None = Field(default=None, max_length=100)
    dose_value: str = Field(min_length=1, max_length=1000)
    dose_unit: str | None = Field(default=None, max_length=50)
    frequency_per_day: str = Field(min_length=1, max_length=1000)
    timing: str | None = Field(default=None, max_length=255)
    duration_days: str = Field(min_length=1, max_length=1000)

    @field_validator("medication_name", "dose_value", "frequency_per_day", "duration_days")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("required value must not be blank")
        return stripped

    @field_validator("medication_strength", "dose_unit", "timing")
    @classmethod
    def optional_text_must_be_normalized(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("dose_value")
    @classmethod
    def dose_value_must_match_prescription_limits(cls, value: str) -> str:
        stripped = value.strip()
        try:
            parsed = Decimal(stripped)
        except InvalidOperation as exc:
            raise ValueError("dose_value must be a positive decimal") from exc
        if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("9999999.999"):
            raise ValueError("dose_value must be a positive decimal")
        exponent = parsed.normalize().as_tuple().exponent
        if not isinstance(exponent, int) or max(-exponent, 0) > 3:
            raise ValueError("dose_value supports up to 3 decimal places")
        return stripped

    @field_validator("frequency_per_day", "duration_days")
    @classmethod
    def integer_value_must_match_prescription_limits(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.isdecimal():
            raise ValueError("value must be a positive integer")
        parsed = int(stripped)
        if parsed <= 0 or parsed > 2_147_483_647:
            raise ValueError("value must be a positive integer")
        return stripped


class ExtractedFieldData(BaseModel):
    field_id: UUID
    field_type: str
    medication_index: int
    raw_value: str | None = None
    normalized_value: str | None = None
    confirmed_value: str | None = None
    confidence_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    confirmation_status: str
    normalization_version: str | None = None


class OcrJobData(BaseModel):
    job_id: UUID
    document_id: UUID
    ocr_status: OcrJobStatus
    error_code: str | None = None
    error_message: str | None = None

    # 실제 OCR과 구조화에 사용한 실행 정보를 반환합니다.
    engine_name: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None

    created_at: datetime
    completed_at: datetime | None = None
    fields: list[ExtractedFieldData] = Field(default_factory=list)


class OcrJobResponse(BaseModel):
    data: OcrJobData


class ExtractedFieldResponse(BaseModel):
    data: ExtractedFieldData
