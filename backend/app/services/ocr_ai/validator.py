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

# 숫자 필드는 OCR 원문의 완전한 숫자 단위와 일치해야 합니다.
# 예: OCR "10회"에서 LLM이 반환한 "1"은 근거로 인정하지 않습니다.
_NUMERIC_GROUNDING_FIELD_TYPES = frozenset(
    {
        "DOSE_VALUE",
        "FREQUENCY_PER_DAY",
        "DURATION_DAYS",
    }
)

# 숫자의 일부를 잘라서 일치시키는 것을 막기 위한 인접 문자입니다.
# 단위 문자는 포함하지 않아 "1정"에서 DOSE_VALUE "1"은 허용합니다.
_NUMERIC_NEIGHBOR_CHARACTERS = r"\d.,_eE"


# 횟수와 기간은 같은 source_ids 안의 아무 숫자가 아니라
# 해당 필드 단위가 바로 뒤에 붙은 숫자만 근거로 인정합니다.
_NUMERIC_CONTEXT_UNIT_PATTERNS = {
    "FREQUENCY_PER_DAY": r"(?:회|번)",
    "DURATION_DAYS": r"(?:일|day(?:s)?)",
}

# 제품 함량의 일부만 잘라서 일치시키는 것을 막습니다.
#
# 예:
# - OCR 100mg / LLM 0mg: 거부
# - OCR 5mg/100mg / LLM 100mg: 거부
# - OCR 약품명100mg / LLM 100mg: 허용
_STRENGTH_NEIGHBOR_CHARACTERS = r"\d.,_/%"


# MEDICATION_NAME은 OCR 원문과 완전히 일치하거나,
# 뒤에 제품 함량만 남는 경우에만 근거가 있는 것으로 판단합니다.
# _comparison_keys() 적용 후 비교하므로 소문자·공백 제거 기준입니다.
_STRENGTH_UNIT_KEY = r"(?:mg|g|mcg|μg|ml|%)"
_STRENGTH_AMOUNT_KEY = rf"\d+(?:\.\d+)?{_STRENGTH_UNIT_KEY}"
_MEDICATION_STRENGTH_SUFFIX_PATTERN = re.compile(
    rf"^\(?"
    rf"{_STRENGTH_AMOUNT_KEY}"
    rf"(?:/(?:{_STRENGTH_AMOUNT_KEY}|{_STRENGTH_UNIT_KEY}))*"
    rf"\)?$"
)


def _contains_complete_medication_strength(
    *,
    generated_compact: str,
    source_compact: str,
) -> bool:
    """
    생성된 제품 함량이 OCR 원문의 완전한 함량 표현인지 확인합니다.

    제품명과 함량이 같은 OCR token에 들어 있는 경우는 허용하지만,
    더 큰 숫자나 복합 함량의 일부를 잘라낸 값은 허용하지 않습니다.
    """
    if not generated_compact:
        return False

    # LLM 값 자체가 지원하는 함량 형식인지 먼저 검증합니다.
    if _MEDICATION_STRENGTH_SUFFIX_PATTERN.fullmatch(generated_compact) is None:
        return False

    pattern = re.compile(
        rf"(?<![{_STRENGTH_NEIGHBOR_CHARACTERS}])"
        rf"{re.escape(generated_compact)}"
        rf"(?![{_STRENGTH_NEIGHBOR_CHARACTERS}])"
    )

    return pattern.search(source_compact) is not None


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


def _contains_complete_numeric_value(
    *,
    generated_compact: str,
    source_compact: str,
) -> bool:
    """
    생성된 숫자가 OCR 숫자의 일부가 아닌 완전한 값인지 확인합니다.

    단위 문자는 숫자 경계에 포함하지 않으므로
    OCR "1정"에서 생성된 "1"은 정상적으로 허용합니다.
    """
    if not generated_compact:
        return False

    pattern = re.compile(
        rf"(?<![{_NUMERIC_NEIGHBOR_CHARACTERS}])"
        rf"{re.escape(generated_compact)}"
        rf"(?![{_NUMERIC_NEIGHBOR_CHARACTERS}])"
    )

    return pattern.search(source_compact) is not None


def _contains_numeric_value_with_field_context(
    *,
    field_type: str,
    generated_compact: str,
    source_compact: str,
) -> bool:
    """
    횟수·기간 숫자가 해당 필드의 단위 문맥에 있는지 확인합니다.

    source가 숫자 token 하나뿐인 경우에는 완전 일치를 허용합니다.
    단위가 포함된 source에서는 숫자 바로 뒤에 필드 단위가 있어야 합니다.

    예:
    - FREQUENCY_PER_DAY=3 / OCR "1일 3회": 허용
    - FREQUENCY_PER_DAY=1 / OCR "1일 3회": 거부
    - DURATION_DAYS=1 / OCR "1일 3회": 허용
    """
    if not generated_compact:
        return False

    # CLOVA가 숫자를 단독 token으로 반환하고 LLM도 그 token만
    # source_id로 참조한 경우에는 정확히 같은 숫자만 허용합니다.
    if generated_compact == source_compact:
        return True

    unit_pattern = _NUMERIC_CONTEXT_UNIT_PATTERNS[field_type]
    pattern = re.compile(
        rf"(?<![{_NUMERIC_NEIGHBOR_CHARACTERS}])"
        rf"{re.escape(generated_compact)}"
        rf"(?={unit_pattern})"
    )

    return pattern.search(source_compact) is not None


def _is_complete_medication_name(
    *,
    generated_compact: str,
    source_compact: str,
) -> bool:
    """
    약품명 전체가 OCR 근거와 일치하는지 확인합니다.

    OCR token 하나에 약품명과 제품 함량이 함께 있는 경우에는
    약품명 뒤에 유효한 제품 함량만 남는 것을 허용합니다.
    """
    if not generated_compact:
        return False

    if generated_compact == source_compact:
        return True

    if not source_compact.startswith(generated_compact):
        return False

    remaining_suffix = source_compact[len(generated_compact) :]

    return _MEDICATION_STRENGTH_SUFFIX_PATTERN.fullmatch(remaining_suffix) is not None


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

    if field_type in _NUMERIC_CONTEXT_UNIT_PATTERNS:
        # 횟수와 기간은 같은 OCR 근거 안의 아무 숫자가 아니라
        # 해당 필드 단위가 붙은 숫자만 허용합니다.
        is_grounded = _contains_numeric_value_with_field_context(
            field_type=field_type,
            generated_compact=generated_compact,
            source_compact=source_compact,
        )
    elif field_type in _NUMERIC_GROUNDING_FIELD_TYPES:
        # DOSE_VALUE 등은 "1정"에서 숫자 1을 분리할 수 있도록
        # 완전한 숫자 경계만 확인합니다.
        is_grounded = _contains_complete_numeric_value(
            generated_compact=generated_compact,
            source_compact=source_compact,
        )
    elif field_type == "MEDICATION_STRENGTH":
        # 제품 함량은 더 큰 숫자나 복합 함량의 일부가 아닌
        # 전체 함량 표현으로 검증합니다.
        is_grounded = _contains_complete_medication_strength(
            generated_compact=generated_compact,
            source_compact=source_compact,
        )
    elif field_type == "MEDICATION_NAME":
        # 약품명 임의 일부만 반환하는 것을 차단합니다.
        # 단, 동일 OCR token 뒤에 제품 함량만 붙은 경우는 허용합니다.
        is_grounded = _is_complete_medication_name(
            generated_compact=generated_compact,
            source_compact=source_compact,
        )
    else:
        # 함량·단위·복용 시점 등은 OCR token 결합에 따른
        # 공백 차이만 허용합니다.
        is_grounded = bool(generated_spaced) and (
            generated_spaced in source_spaced or generated_compact in source_compact
        )

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
