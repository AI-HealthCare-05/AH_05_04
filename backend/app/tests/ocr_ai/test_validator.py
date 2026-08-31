import pytest

from app.services.medication_name_normalizer import MedicationNameNormalizer
from app.services.ocr_ai.schemas import (
    GeneratedMedication,
    GeneratedPrescriptionDraft,
    GeneratedSourceValue,
)
from app.services.ocr_ai.validator import validate_and_convert_draft
from app.services.ocr_engine import OcrProcessingError, RawRecognizedField


def _raw(
    value: str,
    *,
    center_x: float = 10,
    center_y: float = 10,
    height: float = 10,
) -> RawRecognizedField:
    return RawRecognizedField(
        raw_value=value,
        confidence_score=0.99,
        center_x=center_x,
        center_y=center_y,
        height=height,
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

    values = {field.field_type: field.raw_value for field in result}

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
    assert not any(field.field_type == "MEDICATION_STRENGTH" for field in result)


def test_validator_discards_strength_suffix_from_larger_ocr_number() -> None:
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
                    # OCR 100mg의 일부인 0mg을 LLM이 반환한 경우입니다.
                    value="0mg",
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

    # 일부 문자열만 일치하는 잘못된 함량은 저장하지 않습니다.
    assert not any(field.field_type == "MEDICATION_STRENGTH" for field in result)


def test_validator_discards_component_of_compound_strength() -> None:
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
                    # 복합 함량 전체가 아닌 뒤쪽 성분만 반환했습니다.
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

    assert not any(field.field_type == "MEDICATION_STRENGTH" for field in result)


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
    assert not any(field.field_type == "MEDICATION_STRENGTH" for field in result)


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

    values = {field.field_type: field.raw_value for field in result}

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

    values = {field.field_type: field.raw_value for field in result}

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

    values = {field.field_type: field.raw_value for field in result}

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
    timing = next(field for field in result if field.field_type == "TIMING")

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

    frequency = next(field for field in result if field.field_type == "FREQUENCY_PER_DAY")

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

    prescribed_date = next(field for field in result if field.field_type == "PRESCRIBED_DATE")

    assert prescribed_date.raw_value == "2026-08-26"
    assert prescribed_date.normalized_value == "2026-08-26"


def test_validator_rejects_numeric_substring_from_larger_ocr_number() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("10회"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    # OCR은 10회인데 LLM이 일부 숫자인 1만 반환한 경우입니다.
                    value="1",
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

    frequency = next(field for field in result if field.field_type == "FREQUENCY_PER_DAY")

    # 근거 없는 숫자는 저장하지 않고 검수용 빈 필드로 대체합니다.
    assert frequency.raw_value is None
    assert frequency.confidence_score is None


def test_validator_rejects_frequency_from_distant_numeric_and_unit_tokens() -> None:
    raw_fields = [
        _raw("합성의약품에이정", center_x=10, center_y=10),
        _raw("1", center_x=40, center_y=10),
        _raw("회", center_x=100, center_y=10),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="1",
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

    frequency = next(field for field in result if field.field_type == "FREQUENCY_PER_DAY")

    assert frequency.raw_value is None
    assert frequency.confidence_score is None


def test_validator_accepts_frequency_with_frequency_unit_context() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("1일 3회"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="3",
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

    frequency = next(field for field in result if field.field_type == "FREQUENCY_PER_DAY")

    assert frequency.raw_value == "3"
    assert frequency.confidence_score == 0.99


def test_validator_rejects_duration_from_split_frequency_context() -> None:
    raw_fields = [
        _raw("합성의약품에이정", center_x=10, center_y=10),
        _raw("1일", center_x=50, center_y=10),
        _raw("3회", center_x=65, center_y=10),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                duration_days=GeneratedSourceValue(
                    value="1",
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

    duration = next(field for field in result if field.field_type == "DURATION_DAYS")

    assert duration.raw_value is None
    assert duration.confidence_score is None


def test_validator_rejects_duration_from_standalone_numeric_token() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("1"),
        _raw("일"),
        _raw("3"),
        _raw("회"),
        _raw("7"),
        _raw("일분"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                duration_days=GeneratedSourceValue(
                    value="1",
                    # 숫자 token만으로는 기간인지 횟수 문맥인지 알 수 없습니다.
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

    duration = next(field for field in result if field.field_type == "DURATION_DAYS")

    assert duration.raw_value is None
    assert duration.confidence_score is None


def test_validator_accepts_split_numeric_and_duration_unit_tokens() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("7", center_x=40, center_y=10),
        _raw("일분", center_x=52, center_y=10),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                duration_days=GeneratedSourceValue(
                    value="7",
                    # 숫자와 기간 단위를 모두 근거로 제공합니다.
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

    duration = next(field for field in result if field.field_type == "DURATION_DAYS")

    assert duration.raw_value == "7"
    assert duration.confidence_score == 0.99


def test_validator_accepts_split_numeric_and_frequency_unit_tokens() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("3", center_x=40, center_y=10),
        _raw("회", center_x=50, center_y=10),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="3",
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

    frequency = next(field for field in result if field.field_type == "FREQUENCY_PER_DAY")

    assert frequency.raw_value == "3"
    assert frequency.confidence_score == 0.99


@pytest.mark.parametrize(
    "source_value",
    [
        "7일",
        "7일분",
        "7일간",
        "7days",
    ],
)
def test_validator_accepts_duration_with_duration_context(
    source_value: str,
) -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw(source_value),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                duration_days=GeneratedSourceValue(
                    value="7",
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

    duration = next(field for field in result if field.field_type == "DURATION_DAYS")

    assert duration.raw_value == "7"
    assert duration.confidence_score == 0.99


def test_validator_rejects_partial_medication_name() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    # OCR 약품명의 임의 일부만 반환한 경우입니다.
                    value="합성의약품",
                    source_ids=[1],
                ),
            )
        ]
    )

    with pytest.raises(
        OcrProcessingError,
        match="MEDICATION_NAME",
    ):
        validate_and_convert_draft(
            draft=draft,
            raw_fields=raw_fields,
            normalizer=MedicationNameNormalizer(),
        )


def test_validator_allows_dose_value_and_unit_from_same_ocr_token() -> None:
    raw_fields = [
        _raw("합성의약품에이정"),
        _raw("1정"),
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
                    value="정",
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

    values = {field.field_type: field.raw_value for field in result}

    # 정상적인 "1정" 분리는 계속 허용해야 합니다.
    assert values["DOSE_VALUE"] == "1"
    assert values["DOSE_UNIT"] == "정"


def test_validator_allows_medication_name_and_strength_from_same_ocr_token() -> None:
    raw_fields = [
        _raw("합성의약품에이정 100mg"),
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
                    source_ids=[1],
                ),
            )
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    values = {field.field_type: field.raw_value for field in result}

    assert values["MEDICATION_NAME"] == "합성의약품에이정"
    assert values["MEDICATION_STRENGTH"] == "100mg"


def test_validator_rejects_dose_value_from_another_medication_row() -> None:
    raw_fields = [
        _raw("합성의약품에이정", center_y=10),
        _raw("1정", center_y=10),
        _raw("합성의약품비정", center_y=30),
        _raw("2정", center_y=30),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                dose_value=GeneratedSourceValue(
                    # 첫 번째 약제가 두 번째 약제 행의 복용량을 참조합니다.
                    value="2",
                    source_ids=[4],
                ),
            ),
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품비정",
                    source_ids=[3],
                ),
                dose_value=GeneratedSourceValue(
                    value="2",
                    source_ids=[4],
                ),
            ),
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    dose_by_medication = {field.medication_index: field for field in result if field.field_type == "DOSE_VALUE"}

    assert dose_by_medication[1].raw_value is None
    assert dose_by_medication[1].confidence_score is None

    assert dose_by_medication[2].raw_value == "2"
    assert dose_by_medication[2].confidence_score == 0.99


def test_validator_rejects_medication_field_from_another_medication_row() -> None:
    raw_fields = [
        _raw("합성의약품에이정", center_y=10),
        _raw("1회", center_y=10),
        _raw("합성의약품비정", center_y=30),
        _raw("2회", center_y=30),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    # 첫 번째 약제가 두 번째 약제 행의 2회 token을 잘못 참조합니다.
                    value="2",
                    source_ids=[4],
                ),
            ),
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품비정",
                    source_ids=[3],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="2",
                    source_ids=[4],
                ),
            ),
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    frequency_by_medication = {
        field.medication_index: field for field in result if field.field_type == "FREQUENCY_PER_DAY"
    }

    # 다른 약제 행을 참조한 첫 번째 약제의 값은 저장하지 않습니다.
    assert frequency_by_medication[1].raw_value is None
    assert frequency_by_medication[1].confidence_score is None

    # 같은 행을 참조한 두 번째 약제의 값은 유지합니다.
    assert frequency_by_medication[2].raw_value == "2"
    assert frequency_by_medication[2].confidence_score == 0.99


def test_validator_accepts_medication_field_from_same_row() -> None:
    raw_fields = [
        _raw("합성의약품에이정", center_y=10),
        _raw("3회", center_y=10),
        _raw("합성의약품비정", center_y=30),
        _raw("2회", center_y=30),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="3",
                    source_ids=[2],
                ),
            ),
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품비정",
                    source_ids=[3],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="2",
                    source_ids=[4],
                ),
            ),
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    frequency_values = {
        field.medication_index: field.raw_value for field in result if field.field_type == "FREQUENCY_PER_DAY"
    }

    assert frequency_values == {
        1: "3",
        2: "2",
    }


def test_validator_accepts_split_medication_name_tokens_from_same_row() -> None:
    raw_fields = [
        _raw("합성의약품", center_x=10, center_y=10),
        _raw("에이정", center_x=30, center_y=10),
        _raw("1회", center_x=60, center_y=10),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품 에이정",
                    source_ids=[1, 2],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="1",
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

    values = {field.field_type: field.raw_value for field in result}

    assert values["MEDICATION_NAME"] == "합성의약품 에이정"
    assert values["FREQUENCY_PER_DAY"] == "1"


def test_validator_accepts_medication_name_from_adjacent_lines() -> None:
    raw_fields = [
        _raw(
            "오메가-3-산",
            center_x=10,
            center_y=10,
            height=10,
        ),
        _raw(
            "에틸에스테르90연질캡슐",
            center_x=10,
            center_y=20,
            height=10,
        ),
        _raw(
            "2회",
            center_x=80,
            center_y=20,
            height=10,
        ),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="오메가-3-산 에틸에스테르90연질캡슐",
                    source_ids=[1, 2],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="2",
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

    values = {field.field_type: field.raw_value for field in result}

    assert values["MEDICATION_NAME"] == ("오메가-3-산 에틸에스테르90연질캡슐")
    assert values["FREQUENCY_PER_DAY"] == "2"


def test_validator_rejects_medication_name_source_shared_between_medications() -> None:
    raw_fields = [
        _raw("합성", center_y=10),
        _raw("의약품에이정", center_y=30),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성 의약품에이정",
                    source_ids=[1, 2],
                ),
            ),
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="의약품에이정",
                    # 첫 번째 약품명과 같은 OCR token을 중복 사용합니다.
                    source_ids=[2],
                ),
            ),
        ]
    )

    with pytest.raises(
        OcrProcessingError,
        match="MEDICATION_NAME",
    ):
        validate_and_convert_draft(
            draft=draft,
            raw_fields=raw_fields,
            normalizer=MedicationNameNormalizer(),
        )


def test_validator_rejects_field_sources_spanning_medication_rows() -> None:
    raw_fields = [
        _raw("합성의약품에이정", center_y=10),
        _raw("1회", center_y=10),
        _raw("합성의약품비정", center_y=30),
        _raw("2회", center_y=30),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="2",
                    # 같은 약제 행과 다른 약제 행의 token을 함께 참조합니다.
                    source_ids=[2, 4],
                ),
            ),
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품비정",
                    source_ids=[3],
                ),
                frequency_per_day=GeneratedSourceValue(
                    value="2",
                    source_ids=[4],
                ),
            ),
        ]
    )

    result = validate_and_convert_draft(
        draft=draft,
        raw_fields=raw_fields,
        normalizer=MedicationNameNormalizer(),
    )

    frequency_by_medication = {
        field.medication_index: field for field in result if field.field_type == "FREQUENCY_PER_DAY"
    }

    assert frequency_by_medication[1].raw_value is None
    assert frequency_by_medication[2].raw_value == "2"


@pytest.mark.parametrize(
    ("field_center_y", "expected_value"),
    [
        (17.5, "2"),
        (17.6, None),
    ],
)
def test_validator_applies_medication_row_distance_boundary(
    field_center_y: float,
    expected_value: str | None,
) -> None:
    raw_fields = [
        _raw(
            "합성의약품에이정",
            center_y=10,
            height=10,
        ),
        _raw(
            "2회",
            center_y=field_center_y,
            height=10,
        ),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품에이정",
                    source_ids=[1],
                ),
                frequency_per_day=GeneratedSourceValue(
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

    frequency = next(field for field in result if field.field_type == "FREQUENCY_PER_DAY")

    assert frequency.raw_value == expected_value


def test_validator_rejects_medication_name_token_closer_to_next_medication() -> None:
    raw_fields = [
        _raw("오메가-3-산", center_x=10, center_y=10, height=10),
        _raw(
            "에틸에스테르90연질캡슐",
            center_x=10,
            center_y=24,
            height=10,
        ),
        _raw("합성의약품비정", center_x=10, center_y=30, height=10),
    ]
    draft = GeneratedPrescriptionDraft(
        medications=[
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="오메가-3-산 에틸에스테르90연질캡슐",
                    source_ids=[1, 2],
                ),
            ),
            GeneratedMedication(
                medication_name=GeneratedSourceValue(
                    value="합성의약품비정",
                    source_ids=[3],
                ),
            ),
        ]
    )

    with pytest.raises(
        OcrProcessingError,
        match="MEDICATION_NAME",
    ):
        validate_and_convert_draft(
            draft=draft,
            raw_fields=raw_fields,
            normalizer=MedicationNameNormalizer(),
        )
