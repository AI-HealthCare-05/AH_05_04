from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class OcrJobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExecuteOcrRequest(BaseModel):
    force_reprocess: bool = False


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
    created_at: datetime
    completed_at: datetime | None = None
    fields: list[ExtractedFieldData] = Field(default_factory=list)


class OcrJobResponse(BaseModel):
    data: OcrJobData


class ExtractedFieldResponse(BaseModel):
    data: ExtractedFieldData
