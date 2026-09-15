from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

LocalTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


class MealKind(StrEnum):
    BREAKFAST = "BREAKFAST"
    LUNCH = "LUNCH"
    DINNER = "DINNER"


class MealPattern(StrEnum):
    REGULAR = "REGULAR"
    IRREGULAR = "IRREGULAR"
    NOT_USUALLY_EATEN = "NOT_USUALLY_EATEN"


class LifestyleAnchorKind(StrEnum):
    WAKE_UP = "WAKE_UP"
    LEAVE_HOME = "LEAVE_HOME"
    RETURN_HOME = "RETURN_HOME"
    BEDTIME = "BEDTIME"


class LifestyleWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_local_time: LocalTime
    end_local_time: LocalTime
    end_day_offset: int = Field(ge=0, le=1, strict=True)

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        start_hour, start_minute = (int(value) for value in self.start_local_time.split(":"))
        end_hour, end_minute = (int(value) for value in self.end_local_time.split(":"))
        start = start_hour * 60 + start_minute
        end = end_hour * 60 + end_minute + self.end_day_offset * 24 * 60
        if not 0 < end - start < 24 * 60:
            raise ValueError("window duration must be greater than zero and less than 24 hours")
        return self


class LifestyleMeal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MealKind
    pattern: MealPattern
    window: LifestyleWindow | None

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if (self.pattern == MealPattern.REGULAR) != (self.window is not None):
            raise ValueError("only regular meals have a window")
        return self


class LifestyleAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LifestyleAnchorKind
    local_time: LocalTime


class LifestyleDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=1, le=7, strict=True)
    meals: list[LifestyleMeal] = Field(max_length=3)
    anchors: list[LifestyleAnchor] = Field(max_length=4)
    unavailable_windows: list[LifestyleWindow] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_and_sort(self) -> Self:
        meal_kinds = [meal.kind for meal in self.meals]
        if len(meal_kinds) != len(set(meal_kinds)):
            raise ValueError("duplicate meal kind")
        anchor_kinds = [anchor.kind for anchor in self.anchors]
        if len(anchor_kinds) != len(set(anchor_kinds)):
            raise ValueError("duplicate anchor kind")
        window_keys = [
            (window.start_local_time, window.end_day_offset, window.end_local_time)
            for window in self.unavailable_windows
        ]
        if len(window_keys) != len(set(window_keys)):
            raise ValueError("duplicate unavailable window")

        meal_order = {kind: position for position, kind in enumerate(MealKind)}
        self.meals.sort(key=lambda meal: meal_order[meal.kind])
        self.anchors.sort(key=lambda anchor: anchor.kind.value)
        self.unavailable_windows.sort(
            key=lambda window: (window.start_local_time, window.end_day_offset, window.end_local_time)
        )
        return self


class PutLifestyleTimesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0, strict=True)
    days: list[LifestyleDay] = Field(max_length=7)

    @model_validator(mode="after")
    def validate_and_sort(self) -> Self:
        weekdays = [day.weekday for day in self.days]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError("duplicate weekday")
        self.days.sort(key=lambda day: day.weekday)
        return self


class LifestyleTimesData(BaseModel):
    revision: int
    updated_at: AwareDatetime | None
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    days: list[LifestyleDay]


class LifestyleTimesResponse(BaseModel):
    data: LifestyleTimesData
