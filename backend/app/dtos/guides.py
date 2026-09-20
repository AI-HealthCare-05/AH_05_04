from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from rag_runtime.guide_release_projection import (
    GuideRuntimeCitationSourceType,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseDecision,
)


# GUIDE.generation_status와 동일한 값을 사용합니다.
class GuideStatus(StrEnum):
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CreateGuideRequest(BaseModel):
    prescription_id: UUID


class GuideCitationData(BaseModel):
    source_type: GuideRuntimeCitationSourceType
    source_code: str
    source_version: str
    locator: str
    display_order: int


class GuideData(BaseModel):
    guide_id: UUID
    prescription_id: UUID
    prescription_version_id: UUID
    generation_status: GuideStatus
    content: str | None
    model_name: str | None
    prompt_version: str | None
    release_decision: GuideRuntimeReleaseDecision | None
    release_is_current: bool | None
    fallback_code: GuideRuntimeFallbackCode | None
    fallback_text: str | None
    citations: list[GuideCitationData]
    requested_at: datetime
    completed_at: datetime | None


class GuideResponse(BaseModel):
    data: GuideData
