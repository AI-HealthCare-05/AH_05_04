from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class UpdateExtractedFieldRequest(BaseModel):
    # null은 선택 필드를 사용자가 “값 없음”으로 확인한 경우입니다.
    # 필수 필드의 null 거부는 필드 유형을 아는 서비스 계층에서 처리합니다.
    confirmed_value: str | None = Field(min_length=1)


class MedicationData(BaseModel):
    prescription_version_medication_id: UUID
    # 사용자 화면에는 처방전에서 확인한 이름을 그대로 반환합니다.
    medication_name: str

    # 제품 함량은 1회 복용량과 별도로 반환합니다.
    strength_text: str | None = None

    dose_value: float | None = None
    dose_unit: str | None = None
    frequency_per_day: int | None = None
    timing_text: str | None = None
    duration_days: int | None = None
    display_order: int


class PrescriptionData(BaseModel):
    prescription_id: UUID
    prescription_version_id: UUID
    revision: int
    current: bool
    document_id: UUID
    prescribed_date: date
    confirmed_at: datetime
    medications: list[MedicationData] = Field(default_factory=list)


class PrescriptionResponse(BaseModel):
    data: PrescriptionData


class PrescriptionMedicationCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    medication_name: str = Field(min_length=1, max_length=255)
    strength_text: str | None = Field(default=None, max_length=100)
    dose_value: Decimal | None = Field(default=None, gt=0, le=Decimal("9999999.999"), decimal_places=3)
    dose_unit: str | None = Field(default=None, max_length=50)
    frequency_per_day: int | None = Field(default=None, gt=0, le=2_147_483_647)
    timing_text: str | None = Field(default=None, max_length=255)
    duration_days: int | None = Field(default=None, gt=0, le=2_147_483_647)
    display_order: int = Field(gt=0, le=2_147_483_647)

    @field_validator("medication_name")
    @classmethod
    def medication_name_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("medication_name must not be blank")
        return stripped

    @field_validator("strength_text", "dose_unit", "timing_text")
    @classmethod
    def optional_text_must_be_normalized(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class CorrectPrescriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version_id: UUID
    expected_revision: int = Field(gt=0)
    prescribed_date: date
    medications: list[PrescriptionMedicationCorrectionRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def display_orders_must_be_unique(self) -> "CorrectPrescriptionRequest":
        orders = [medication.display_order for medication in self.medications]
        if len(set(orders)) != len(orders):
            raise ValueError("medication display_order must be unique")
        return self
