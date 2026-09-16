from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.track_c import ActionPlanFollowupResponse, BarrierCode, SupportActionPlanStatus, SupportCode

TravelSituation = Literal["SCHEDULE_CHANGED", "MEDICATION_NOT_WITH_ME"]


class ReminderParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: Literal["MEDICATION_SCHEDULE_SETUP"]
    prescription_version_medication_id: UUID


class GuidanceParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_key: Literal[
        "ROUTINE_OR_TRAVEL_PLAN",
        "INSTRUCTION_REVIEW",
        "PURPOSE_REVIEW",
        "MEDICATION_CONCERN_GUIDANCE",
        "ACCESS_SUPPORT",
    ]


class ActionConfigSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["track-c-handler-config-v1"]
    rationale_code: str
    parameters: ReminderParameters | GuidanceParameters


class SupportCopyData(BaseModel):
    title: str
    body: str
    confirmation_prompt: str
    primary_label: str
    secondary_label: str


class SupportOfferItem(BaseModel):
    support_code: SupportCode
    rule_version: str
    copy_version: str
    priority: int
    rationale_code: str
    action_config: ActionConfigSnapshot
    support_copy: SupportCopyData


class SupportOfferData(BaseModel):
    barrier_response_id: UUID
    medication_checkin_id: UUID
    checkin_revision: int
    safety_assessment_id: UUID
    supports: list[SupportOfferItem] = Field(max_length=1)
    reason_code: Literal["NO_ELIGIBLE_SUPPORT"] | None


class SupportOfferResponse(BaseModel):
    data: SupportOfferData


class CreateSupportActionPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barrier_response_id: UUID
    support_code: SupportCode
    rule_version: str = Field(min_length=1, max_length=100)
    copy_version: str = Field(min_length=1, max_length=100)
    travel_situation: TravelSituation | None = None
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def require_explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit user confirmation required")
        return value


class SupportActionPlanData(BaseModel):
    support_action_plan_id: UUID
    barrier_response_id: UUID
    support_code: SupportCode
    rule_version: str
    copy_version: str
    action_config_snapshot: ActionConfigSnapshot
    status: SupportActionPlanStatus
    created_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None


class SupportActionPlanResponse(BaseModel):
    data: SupportActionPlanData


class PatchSupportActionPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["COMPLETED", "CANCELLED"]
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def require_explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit user confirmation required")
        return value


class SubmitActionPlanFollowupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: ActionPlanFollowupResponse
    expected_revision: int = Field(ge=0, strict=True)


class ActionPlanFollowupData(BaseModel):
    followup_id: UUID
    support_action_plan_id: UUID
    response: ActionPlanFollowupResponse
    revision: int
    created_at: datetime
    updated_at: datetime


class ActionPlanFollowupEnvelope(BaseModel):
    data: ActionPlanFollowupData


class ActionPlanFollowupReadEnvelope(BaseModel):
    data: ActionPlanFollowupData | None


class SupportPlanResourcesData(BaseModel):
    support_action_plan_id: UUID
    barrier_code: BarrierCode
    occurrence_id: UUID
    occurrence_local_date: date
    prescription_version_medication_id: UUID
    support_copy: SupportCopyData


class SupportPlanResourcesResponse(BaseModel):
    data: SupportPlanResourcesData
