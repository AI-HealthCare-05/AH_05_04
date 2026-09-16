from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

FeedbackRating = Literal["POSITIVE", "NEGATIVE"]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if "\x00" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("Invalid comment text")
        return value.strip() or None


class FeedbackData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rating: FeedbackRating
    created_at: datetime
    updated_at: datetime


class FeedbackResponse(BaseModel):
    data: FeedbackData
