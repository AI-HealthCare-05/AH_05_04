from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictGeneratedModel(BaseModel):
    """LLM이 정의되지 않은 필드를 추가하지 못하게 합니다."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )


class OcrSourceToken(BaseModel):
    """CLOVA OCR이 반환한 원문 token과 위치 정보입니다."""

    source_id: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1000)
    center_x: float
    center_y: float
    height: float
    confidence: float | None = Field(default=None, ge=0, le=1)


class OcrStructureInput(BaseModel):
    tokens: list[OcrSourceToken] = Field(
        min_length=1,
        max_length=2000,
    )


class GeneratedSourceValue(StrictGeneratedModel):
    """추출 값과 그 값이 나온 CLOVA token을 함께 반환합니다."""

    value: str = Field(min_length=1, max_length=1000)
    source_ids: list[int] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_source_ids(self) -> "GeneratedSourceValue":
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must not contain duplicates")
        return self


class GeneratedMedication(StrictGeneratedModel):
    # 처방전에 적힌 제품명 또는 성분명만 반환합니다.
    medication_name: GeneratedSourceValue

    # 제품 함량입니다. 1회 복용량과 구분합니다.
    strength_text: GeneratedSourceValue | None = None

    # 아래 필드는 실제 복용 지시입니다.
    dose_value: GeneratedSourceValue | None = None
    dose_unit: GeneratedSourceValue | None = None
    frequency_per_day: GeneratedSourceValue | None = None
    duration_days: GeneratedSourceValue | None = None
    timing: GeneratedSourceValue | None = None


class GeneratedPrescriptionDraft(StrictGeneratedModel):
    prescribed_date: GeneratedSourceValue | None = None
    medications: list[GeneratedMedication] = Field(
        default_factory=list,
        max_length=50,
    )


class ProviderOcrStructureResponse(BaseModel):
    draft: GeneratedPrescriptionDraft
    model_name: str = Field(min_length=1, max_length=100)
