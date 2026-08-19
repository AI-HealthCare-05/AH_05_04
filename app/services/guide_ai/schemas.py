import re
import unicodedata
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

_FORBIDDEN_DISPLAY_CHARACTERS = frozenset(
    {
        "\x00",
        "\u200b",
        "\u200c",
        "\u200d",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
        "\ufeff",
    }
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize_display_text(value: str) -> str:
    normalized = _WHITESPACE_PATTERN.sub(" ", unicodedata.normalize("NFC", value).strip())
    if not normalized or any(character in normalized for character in _FORBIDDEN_DISPLAY_CHARACTERS):
        raise ValueError("display text contains an unsafe or empty value")
    return normalized


def _normalize_generated_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("generated text must be a string")
    normalized = unicodedata.normalize("NFC", value.strip())
    return re.sub(r" {2,}", " ", normalized)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _StrictGeneratedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MedicationInput(_StrictModel):
    medication_name: str
    dose_value: Decimal | None = Field(default=None, gt=0)
    dose_unit: str | None = None
    frequency_per_day: int | None = Field(default=None, gt=0)
    timing_text: str | None = None
    duration_days: int | None = Field(default=None, gt=0)

    @field_validator("medication_name")
    @classmethod
    def normalize_medication_name(cls, value: str) -> str:
        return _normalize_display_text(value)

    @field_validator("dose_unit", "timing_text")
    @classmethod
    def normalize_optional_display_text(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _normalize_display_text(value)


class GuideGenerationInput(_StrictModel):
    medications: list[MedicationInput] = Field(min_length=1)


JsonDecimal = Annotated[Decimal, PlainSerializer(lambda value: str(value), return_type=str, when_used="json")]


class MedicationPromptItem(_StrictModel):
    source_index: int = Field(ge=0)
    medication_name: str
    dose_value: JsonDecimal | None = None
    dose_unit: str | None = None
    frequency_per_day: int | None = None
    timing_text: str | None = None
    duration_days: int | None = None


class GeneratedMedicationGuidance(_StrictGeneratedModel):
    source_index: int = Field(ge=0)
    guidance: str = Field(min_length=1, max_length=150)

    @field_validator("guidance", mode="before")
    @classmethod
    def strip_guidance(cls, value: object) -> str:
        stripped = _normalize_generated_text(value)
        if not stripped:
            raise ValueError("guidance must not be blank")
        return stripped


class GeneratedGuideDraft(_StrictGeneratedModel):
    medications: list[GeneratedMedicationGuidance]
    general_notice: str = Field(min_length=1, max_length=300)

    @field_validator("general_notice", mode="before")
    @classmethod
    def strip_general_notice(cls, value: object) -> str:
        stripped = _normalize_generated_text(value)
        if not stripped:
            raise ValueError("general_notice must not be blank")
        return stripped


class ProviderGuideResponse(_StrictModel):
    draft: GeneratedGuideDraft
    model_name: str


class GuideGenerationResult(_StrictModel):
    content: str = Field(min_length=1, max_length=10_000)
    model_name: str = Field(min_length=1, max_length=100)
    prompt_version: str
