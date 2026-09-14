from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OcrConsentState(BaseModel):
    purpose: Literal["OCR"] = "OCR"
    status: Literal["MISSING", "GRANTED", "WITHDRAWN"]
    effective: bool
    reason: Literal["MISSING_CONSENT", "WITHDRAWN", "POLICY_VERSION_MISMATCH"] | None
    current_policy_version: str
    accepted_policy_version: str | None
    granted_at: datetime | None
    withdrawn_at: datetime | None


class OcrConsentResponse(BaseModel):
    data: OcrConsentState


class GrantOcrConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: str = Field(min_length=1, max_length=100)
