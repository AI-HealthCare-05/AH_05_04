import pytest

from app.services.medication_name_normalizer import MedicationNameNormalizer
from app.services.ocr_ai.schemas import (
    GeneratedMedication,
    GeneratedPrescriptionDraft,
    GeneratedSourceValue,
)
from app.services.ocr_ai.validator import validate_and_convert_draft
from app.services.ocr_engine import OcrProcessingError, RawRecognizedField


def _raw(value: str) -> RawRecognizedField:
    return RawRecognizedField(
        raw_value=value,
        confidence_score=0.99,
        center_x=10,
        center_y=10,
        height=10,
    )


def test_validator_separates_medication_name_and_strength() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("100mg"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                strength_text=GeneratedSourceValue(
                    value="100mg",
                    source_ids=[2],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    values = {
        field.field_type: field.raw_value
        for field in result
    }

    assert values["MEDICATION_NAME"] == "합성의약품에이정"
    assert values["MEDICATION_STRENGTH"] == "100mg"


def test_validator_discards_changed_decimal_strength() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("1.0mg"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                strength_text=GeneratedSourceValue(
                    # OCR 원문은 1.0mg인데 LLM이 10mg으로 바꾼 경우입니다.
                    value="10mg",
                    source_ids=[2],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    # 근거 없는 함량은 저장하지 않지만 OCR 전체는 실패시키지 않습니다.
    assert not any(
        field.field_type == "MEDICATION_STRENGTH"
        for field in result
    )

def test_validator_discards_changed_compound_strength() -> None:
    raw_fields = [
        _raw("합성복합정"),
        _raw("5mg/100mg"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성복합정",
                    source_ids=[1],
                ),
                strength_text=GeneratedSourceValue(
                    # 슬래시가 사라진 잘못된 함량입니다.
                    value="5mg100mg",
                    source_ids=[2],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    # 잘못 변경된 함량은 결과에서 제외합니다.
    assert not any(
        field.field_type == "MEDICATION_STRENGTH"
        for field in result
    )

def test_validator_rejects_unknown_source_id() -> None:
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[999],
                ),
            )
        ]
    )

    with pytest.raises(OcrProcessingError):
        validate_and_convert_draft(
            draft=draft,
            raw_fields=[_raw("합성의약품에이정")],
            normalizer=MedicationNameNormalizer(),
        )

def test_validator_allows_whitespace_between_split_ocr_tokens() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("1"),
        _raw("정"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                dose_value=GeneratedSourceValue(
                    value="1",
                    source_ids=[2],
                ),
                dose_unit=GeneratedSourceValue(
                    # CLOVA는 "1"과 "정"을 분리했지만
                    # LLM은 붙여서 근거를 표현할 수 있습니다.
                    value="정",
                    source_ids=[3],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    values = {
        field.field_type: field.raw_value
        for field in result
    }

    assert values["DOSE_VALUE"] == "1"
    assert values["DOSE_UNIT"] == "정"

def test_validator_allows_joined_value_from_split_tokens() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("100"),
        _raw("mg"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                strength_text=GeneratedSourceValue(
                    value="100mg",
                    source_ids=[2, 3],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    values = {
        field.field_type: field.raw_value
        for field in result
    }

    assert values["MEDICATION_STRENGTH"] == "100mg"

def test_validator_allows_timing_list_separator_added_by_llm() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("아침"),
        _raw("저녁"),
        _raw("식후"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                timing=GeneratedSourceValue(
                    # CLOVA token 사이에 없던 나열 기호를 LLM이 넣은 경우입니다.
                    value="아침·저녁 식후",
                    source_ids=[2, 3, 4],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    values = {
        field.field_type: field.raw_value
        for field in result
    }

    assert values["TIMING"] == "아침·저녁 식후"

def test_validator_replaces_changed_timing_with_empty_field() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("아침"),
        _raw("식후"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                timing=GeneratedSourceValue(
                    # OCR 원문에 없는 "저녁"을 생성한 경우입니다.
                    value="저녁 식후",
                    source_ids=[2, 3],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    # 선택 필드인 TIMING은 근거가 없으면 저장하지 않습니다.
    timing = next(
        field
        for field in result
        if field.field_type == "TIMING"
    )

    # 근거 없는 LLM 값은 저장하지 않고
    # 사용자가 직접 입력할 빈 복용 조건 필드를 만듭니다.
    assert timing.raw_value is None
    assert timing.confidence_score is None

def test_validator_replaces_ungrounded_frequency_with_empty_field() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("1회"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    # OCR 원문에는 1회인데 LLM이 2회로 잘못 추출했습니다.
                    value="2",
                    source_ids=[2],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    frequency = next(
        field
        for field in result
        if field.field_type == "FREQUENCY_PER_DAY"
    )

    # 잘못 추출한 2를 저장하지 않고 사용자 입력용 빈 필드로 만듭니다.
    assert frequency.raw_value is None
    assert frequency.confidence_score is None

def test_validator_allows_equivalent_date_separators() -> None:
    raw_fields = [
        _raw("2026.08.26"),
        _raw("합성의약품에이정"),
    ]
    draft = GeneratedPrescriptionDraft(
        prescribed_date=GeneratedSourceValue(
            value="2026-08-26",
            source_ids=[1],
        ),
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[2],
                ),
            )
        ],
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    prescribed_date = next(
        field
        for field in result
        if field.field_type == "PRESCRIBED_DATE"
    )

    assert prescribed_date.raw_value == "2026-08-26"
    assert prescribed_date.normalized_value == "2026-08-26"
