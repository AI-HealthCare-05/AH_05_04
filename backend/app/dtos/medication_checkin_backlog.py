from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class UnconfirmedCheckinItem(BaseModel):
    checkin_id: UUID
    occurrence_id: UUID
    prescription_id: UUID
    prescription_version_id: UUID
    prescription_version_medication_id: UUID
    medication_name: str
    strength_text: str | None
    scheduled_local_date: date
    scheduled_at: datetime
    confirmation_deadline_at: datetime
    status: Literal["UNCONFIRMED"] = "UNCONFIRMED"
    revision: int = Field(ge=1)


class UnconfirmedCheckinPage(BaseModel):
    items: list[UnconfirmedCheckinItem]
    next_cursor: UUID | None


class UnconfirmedCheckinResponse(BaseModel):
    data: UnconfirmedCheckinPage
