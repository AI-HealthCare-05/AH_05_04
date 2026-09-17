from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

LocalTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
Meal = Literal["BREAKFAST", "LUNCH", "DINNER"]


class RecommendationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meal_end_times: dict[Meal, LocalTime] = Field(max_length=3)
    same_times_every_day: Literal[True]

    @field_validator("same_times_every_day", mode="before")
    @classmethod
    def require_explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("explicit daily confirmation required")
        return value


class RecommendationContext(RecommendationInput):
    rule_version: str = Field(min_length=1, max_length=80)


class RecommendationData(BaseModel):
    prescription_version_medication_id: UUID
    prescription_version_id: UUID
    rule_version: str
    timing_text: str | None
    local_times: list[LocalTime]
    reason: Literal[
        "EXPLICIT_AFTER_MEAL",
        "UNSUPPORTED_INSTRUCTION",
        "FREQUENCY_MISMATCH",
        "MISSING_MEAL_END",
        "DAY_BOUNDARY",
        "DUPLICATE_TIME",
    ]


class RecommendationResponse(BaseModel):
    data: RecommendationData
