from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator


class CreateReminderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_at: AwareDatetime

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def require_timestamp_string(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("scheduled_at must be an RFC3339 timestamp")
        if isinstance(value, str) and "T" not in value.upper():
            raise ValueError("scheduled_at must be an RFC3339 timestamp")
        return value

    @field_validator("scheduled_at")
    @classmethod
    def utc_instant(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ReadNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationData(BaseModel):
    id: UUID
    occurrence_id: UUID
    occurrence_local_date: date
    kind: Literal["SCHEDULED", "REMINDER"]
    scheduled_at: datetime
    status: Literal["DELIVERED"]
    delivered_at: datetime
    read_at: datetime | None


class NotificationResponse(BaseModel):
    data: NotificationData


class NotificationListData(BaseModel):
    items: list[NotificationData]
    next_offset: int | None


class NotificationListResponse(BaseModel):
    data: NotificationListData


class ReminderData(BaseModel):
    id: UUID
    occurrence_id: UUID
    kind: Literal["REMINDER"] = "REMINDER"
    scheduled_at: datetime
    status: Literal["PENDING"] = "PENDING"


class ReminderResponse(BaseModel):
    data: ReminderData
