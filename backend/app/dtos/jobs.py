from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.async_jobs import AiJobStatus, AiJobType, DomainType


class JobErrorData(BaseModel):
    code: str
    message: str


class JobStatusData(BaseModel):
    job_id: UUID
    job_type: AiJobType
    status: AiJobStatus
    domain_type: DomainType
    domain_id: UUID
    prescription_version_id: UUID | None
    status_url: str
    result_url: str | None
    retry_after_seconds: int | None
    error: JobErrorData | None
    created_at: datetime
    updated_at: datetime


class JobStatusResponse(BaseModel):
    data: JobStatusData
