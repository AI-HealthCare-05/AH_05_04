from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


# GUIDE.generation_status와 동일한 값을 사용합니다.
class GuideStatus(StrEnum):
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CreateGuideRequest(BaseModel):
    prescription_id: UUID


class GuideData(BaseModel):
    guide_id: UUID
    prescription_id: UUID
    generation_status: GuideStatus
    content: str | None
    model_name: str | None
    prompt_version: str | None
    requested_at: datetime
    completed_at: datetime | None


class GuideResponse(BaseModel):
    data: GuideData
