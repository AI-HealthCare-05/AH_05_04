import re
import unicodedata

from app.services.medication_name_normalizer import (
    MedicationNameNormalizer,
)
from app.services.ocr_ai.schemas import (
    GeneratedPrescriptionDraft,
    GeneratedSourceValue,
)
from app.services.ocr_engine import (
    OcrProcessingError,
    RawRecognizedField,
    RecognizedField,
)

_DATE_PATTERN = re.compile(
    r"(?P<year>\d{4})\D+"
    r"(?P<month>\d{1,2})\D+"
    r"(?P<day>\d{1,2})"
)

_WHITESPACE_PATTERN = re.compile(r"\s+")
# LLM이 값을 찾지 못하거나 grounding 검증에 실패했을 때
# 검수 화면에 사용자 입력용 빈칸을 제공하는 필드입니다.
# 처방 확정 필수 여부와는 별개이며 TIMING은 선택값입니다.
_EMPTY_REVIEW_FIELD_TYPES = frozenset(
    {
        "DOSE_VALUE",
        "FREQUENCY_PER_DAY",
        "DURATION_DAYS",
        # 복용 조건 인식에 실패해도 필드를 없애지 않고
        # 사용자가 원본을 보고 직접 입력할 빈칸을 제공합니다.
        "TIMING",
    }
)

# 복용 시점에서 항목을 나열할 때 사용되는 표기 차이입니다.
# 예: "아침 저녁", "아침·저녁", "아침, 저녁"
_TIMING_SEPARATOR_PATTERN = re.compile(r"[\s,·ㆍ/()\[\]]+")


def _comparison_keys(value: str) -> tuple[str, str]:
    """
    OCR token 분리 때문에 생기는 공백 차이만 허용합니다.

    spaced_key:
        연속 공백을 하나로 정리한 비교값입니다.

    compact_key:
        CLOVA가 '1'과 '정'을 별도 token으로 반환하는 경우를 위해
        공백만 제거한 비교값입니다.

    소수점, 슬래시, 하이픈 등 의미 있는 문자는 제거하지 않습니다.
    따라서 1.0mg과 10mg, 5mg/100mg과 5mg100mg은 계속 구분됩니다.
    """
    normalized = unicodedata.normalize(
        "NFKC",
        value,
    ).casefold()

    spaced_key = _WHITESPACE_PATTERN.sub(
        " ",
        normalized,
    ).strip()

    compact_key = _WHITESPACE_PATTERN.sub(
        "",
        normalized,
    )

    return spaced_key, compact_key


def _timing_comparison_key(value: str) -> str:
    """
    TIMING 필드의 나열 구분 기호 차이만 제거합니다.

    CLOVA가 여러 token으로 반환한 복용 시점을 LLM이
    '아침·저녁 식후'처럼 조합하는 경우를 허용합니다.

    글자와 숫자는 제거하지 않으므로
    '아침 식후'를 '저녁 식후'로 바꾸는 것은 허용되지 않습니다.
    """
    normalized = unicodedata.normalize(
        "NFKC",
        value,
    ).casefold()

    return _TIMING_SEPARATOR_PATTERN.sub(
        "",
        normalized,
    )


def _source_fields(
    *,
    generated: GeneratedSourceValue,
    source_map: dict[int, RawRecognizedField],
) -> list[RawRecognizedField]:
    fields: list[RawRecognizedField] = []

    for source_id in generated.source_ids:
        source = source_map.get(source_id)

        if source is None:
            raise OcrProcessingError("LLM 구조화 결과가 존재하지 않는 OCR token을 참조했습니다.")

        fields.append(source)

    return fields


def _validate_grounded_value(
    *,
    field_type: str,
    generated: GeneratedSourceValue,
    source_fields: list[RawRecognizedField],
) -> None:
    """
    LLM이 반환한 값이 참조한 CLOVA token 안에 실제로 존재하는지 검증합니다.

    OCR token 경계에서 생긴 공백 차이만 허용하고,
    숫자와 문장부호 변경은 허용하지 않습니다.
    """
    generated_spaced, generated_compact = _comparison_keys(generated.value)

    source_value = " ".join(field.raw_value for field in source_fields)
    source_spaced, source_compact = _comparison_keys(source_value)

    is_grounded = generated_spaced and (generated_spaced in source_spaced or generated_compact in source_compact)

    # TIMING은 CLOVA가 "아침", "저녁", "식후"를 각각 반환하고
    # LLM이 "아침·저녁 식후"처럼 구분 기호를 넣어 조합할 수 있습니다.
    # 이 경우에만 나열 구분 기호 차이를 추가로 허용합니다.
    if not is_grounded and field_type == "TIMING":
        generated_timing_key = _timing_comparison_key(generated.value)
        source_timing_key = _timing_comparison_key(source_value)

        is_grounded = bool(generated_timing_key) and generated_timing_key in source_timing_key
    # 날짜는 CLOVA와 LLM의 구분 기호가 달라도
    # 실제 연·월·일이 같으면 동일한 OCR 근거로 인정합니다.
    # 예: 2026.08.26, 2026/08/26, 2026-08-26
    if not is_grounded and field_type == "PRESCRIBED_DATE":
        generated_date = _normalize_date(generated.value)
        source_date = _normalize_date(source_value)

        is_grounded = generated_date is not None and source_date is not None and generated_date == source_date

    if not is_grounded:
        raise OcrProcessingError(f"LLM 구조화 결과의 {field_type} 값에 OCR 원문 근거가 없습니다.")


def _minimum_confidence(
    fields: list[RawRecognizedField],
) -> float | None:
    values = [field.confidence_score for field in fields if field.confidence_score is not None]

    return min(values) if values else None


def _normalize_date(value: str) -> str | None:
    """처방일 원문은 보존하고 normalized_value만 YYYY-MM-DD로 변환합니다."""

    match = _DATE_PATTERN.search(value)

    if match is None:
        return None

    return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"


def _make_empty_review_field(
    *,
    medication_index: int,
    field_type: str,
) -> RecognizedField:
    """
    검수용 빈 필드 대상으로 지정한 값을 사용자 입력용 빈 필드로 만듭니다.
    검증에 실패한 LLM 값은 저장하지 않습니다.
    """
    return RecognizedField(
        medication_index=medication_index,
        field_type=field_type,
        raw_value=None,
        normalized_value=None,
        normalization_version=None,
        confidence_score=None,
    )


def _make_field(
    *,
    medication_index: int,
    field_type: str,
    generated: GeneratedSourceValue,
    source_map: dict[int, RawRecognizedField],
    normalizer: MedicationNameNormalizer,
) -> RecognizedField:
    source_fields = _source_fields(
        generated=generated,
        source_map=source_map,
    )

    _validate_grounded_value(
        field_type=field_type,
        generated=generated,
        source_fields=source_fields,
    )

    raw_value = generated.value.strip()
    normalized_value: str | None = None
    normalization_version: str | None = None

    if field_type == "MEDICATION_NAME":
        # LLM이 제품 함량을 분리한 뒤의 약품명만 규칙 기반으로 표기를 정리합니다.
        normalized = normalizer.normalize(raw_value)
        normalized_value = normalized.normalized_value
        normalization_version = normalized.normalization_version

    elif field_type == "PRESCRIBED_DATE":
        normalized_value = _normalize_date(raw_value)

        if normalized_value is not None:
            normalization_version = "date-rule-v1"

    return RecognizedField(
        medication_index=medication_index,
        field_type=field_type,
        raw_value=raw_value,
        normalized_value=normalized_value,
        normalization_version=normalization_version,
        confidence_score=_minimum_confidence(source_fields),
    )


def validate_and_convert_draft(
    *,
    draft: GeneratedPrescriptionDraft,
    raw_fields: list[RawRecognizedField],
    normalizer: MedicationNameNormalizer,
) -> list[RecognizedField]:
    """전체 LLM 결과를 검증하고 DB 저장용 RecognizedField로 변환합니다."""

    if not draft.medications:
        raise OcrProcessingError("OCR 구조화 결과에서 약품을 찾을 수 없습니다.")

    source_map = {
        source_id: raw_field
        for source_id, raw_field in enumerate(
            raw_fields,
            start=1,
        )
    }

    result: list[RecognizedField] = []

    if draft.prescribed_date is None:
        # 처방일을 인식하지 못해도 검수 화면에서 직접 입력할 수 있게 합니다.
        result.append(
            _make_empty_review_field(
                medication_index=0,
                field_type="PRESCRIBED_DATE",
            )
        )
    else:
        try:
            result.append(
                _make_field(
                    medication_index=0,
                    field_type="PRESCRIBED_DATE",
                    generated=draft.prescribed_date,
                    source_map=source_map,
                    normalizer=normalizer,
                )
            )
        except OcrProcessingError:
            # 근거 검증에 실패한 날짜는 사용하지 않고 빈칸으로 대체합니다.
            result.append(
                _make_empty_review_field(
                    medication_index=0,
                    field_type="PRESCRIBED_DATE",
                )
            )

    for medication_index, medication in enumerate(
        draft.medications,
        start=1,
    ):
        # 사용자 화면에 표시할 처방전상의 제품명 또는 성분명입니다.
        result.append(
            _make_field(
                medication_index=medication_index,
                field_type="MEDICATION_NAME",
                generated=medication.medication_name,
                source_map=source_map,
                normalizer=normalizer,
            )
        )

        optional_fields = (
            # 제품 함량은 1회 복용량과 별도 필드로 저장합니다.
            ("MEDICATION_STRENGTH", medication.strength_text),
            ("DOSE_VALUE", medication.dose_value),
            ("DOSE_UNIT", medication.dose_unit),
            ("FREQUENCY_PER_DAY", medication.frequency_per_day),
            ("DURATION_DAYS", medication.duration_days),
            ("TIMING", medication.timing),
        )

        for field_type, generated in optional_fields:
            if generated is None:
                # 빈 검수 필드 대상으로 정한 유형은 LLM이 찾지 못해도
                # 사용자가 원본을 보고 직접 입력할 수 있게 합니다.
                if field_type in _EMPTY_REVIEW_FIELD_TYPES:
                    result.append(
                        _make_empty_review_field(
                            medication_index=medication_index,
                            field_type=field_type,
                        )
                    )
                continue

            try:
                result.append(
                    _make_field(
                        medication_index=medication_index,
                        field_type=field_type,
                        generated=generated,
                        source_map=source_map,
                        normalizer=normalizer,
                    )
                )
            except OcrProcessingError:
                # 근거 없는 LLM 값은 절대 저장하지 않습니다.
                if field_type in _EMPTY_REVIEW_FIELD_TYPES:
                    # 검수 대상으로 정한 필드는 사용자 입력용 빈 필드로 대체합니다.
                    result.append(
                        _make_empty_review_field(
                            medication_index=medication_index,
                            field_type=field_type,
                        )
                    )
                continue

    return result
