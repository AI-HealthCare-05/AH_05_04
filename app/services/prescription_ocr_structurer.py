import re
from statistics import median

from app.services.medication_name_normalizer import (
    MedicationNameNormalizer,
)
from app.services.ocr_engine import (
    RawRecognizedField,
    RecognizedField,
)

_DATE_PATTERN = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")
# 숫자 1을 I, l, |로 오인식한 경우에도 원문을 삭제하지 않고
# 미확인 필드로 남길 수 있도록 허용합니다.
_OCR_NUMBER = r"(?:\d+(?:\.\d+)?|[Iil|])"

# 임의의 한글 단위를 허용하지 않고 처방전에서 실제 투여량으로
# 사용할 수 있는 단위만 명시적으로 허용합니다.
DOSE_PATTERN = re.compile(
    rf"(?P<value>{_OCR_NUMBER})\s*"
    r"(?P<unit>"
    r"mg|g|mcg|µg|μg|㎍|㎎|"
    r"mL|ml|㎖|"
    r"정|캡슐|포|병|앰플|바이알|방울"
    r")"
    r"(?:씩)?"
    r"(?=\s|$)",
    re.IGNORECASE,
)

_DOSE_INSTRUCTION_PATTERN = re.compile(
    rf"^\s*{_OCR_NUMBER}\s*"
    r"(?:"
    r"mg|g|mcg|µg|μg|㎍|㎎|"
    r"mL|ml|㎖|"
    r"정|캡슐|포|병|앰플|바이알|방울"
    r")"
    r"(?:씩)?\s*"
    r"(?:복용|투여|드세요|먹으세요)"
    r"\s*$",
    re.IGNORECASE,
)

_FREQUENCY_PATTERN = re.compile(
    rf"(?P<value>{_OCR_NUMBER})\s*회(?:씩)?(?=\s|$)",
    re.IGNORECASE,
)

# 수정: 일, 일간뿐 아니라 처방전에서 사용하는 일분·일치도
# 동일한 투약기간 값으로 추출합니다.
_DURATION_PATTERN = re.compile(
    r"(?P<value>\d+)\s*일(?:간|분|치)?(?=\s|$)",
    re.IGNORECASE,
)

# 수정: 여러 줄 약품명에서 공통으로 사용할 함량과 포장·제형 패턴입니다.
_STRENGTH_UNIT_TEXT = r"(?:mg|g|mcg|µg|μg|㎍|㎎|mL|ml|㎖|%)"
_STRENGTH_TEXT = rf"\d+(?:\.\d+)?\s*{_STRENGTH_UNIT_TEXT}"
_PACKAGE_FORM_TEXT = (
    r"(?:연질|경질)?"
    r"(?:캡슐|정|포|병|앰플|바이알)"
)

# 수정: OCR이 제형·함량 사이의 공백을 누락해도 연속 약품명으로 처리합니다.
# 정500mg, 90연질캡슐1000mg과 공백이 있는 형태를 모두 허용합니다.
_PACKAGE_CONTINUATION_PATTERN = re.compile(
    rf"^(?:"
    rf"{_STRENGTH_TEXT}"
    rf"|{_PACKAGE_FORM_TEXT}\s*{_STRENGTH_TEXT}"
    rf"|\d+\s*{_PACKAGE_FORM_TEXT}"
    rf"(?:\s*{_STRENGTH_TEXT})?"
    rf")$",
    re.IGNORECASE,
)

# 약품명으로 판단할 수 있는 적극적인 근거입니다.
_DOSAGE_FORM_PATTERN = re.compile(
    r"(?:"
    r"정|캡슐|연질캡슐|경질캡슐|시럽|과립|"
    r"연고|크림|겔|패치|주사|주사액|점안액|현탁액"
    r")"
    r"(?:\s|$|\d)",
    re.IGNORECASE,
)

# 수정: 약품명 판단과 연속 행 판단에서 동일한 함량 표현을 사용합니다.
_STRENGTH_PATTERN = re.compile(
    _STRENGTH_TEXT,
    re.IGNORECASE,
)
# 단일 함량과 복합 함량을 하나의 제품 함량 문자열로 처리합니다.
_STRENGTH_SEQUENCE_TEXT = (
    rf"{_STRENGTH_TEXT}"
    rf"(?:\s*/\s*{_STRENGTH_TEXT})*"
)

# 약품명 끝의 일반 함량과 괄호로 둘러싸인 함량을 분리합니다.
# 예: "로수바스타틴정 10mg", "복합정 5mg/100mg",
#     "에제티미브정 (10mg)"
_TRAILING_STRENGTH_PATTERN = re.compile(
    rf"(?:"
    rf"\(\s*(?P<parenthesized_strength>{_STRENGTH_SEQUENCE_TEXT})\s*\)"
    rf"|(?P<plain_strength>{_STRENGTH_SEQUENCE_TEXT})"
    rf")\s*$",
    re.IGNORECASE,
)

_TIMING_PATTERN = re.compile(
    r"(?:"
    r"아침|점심|저녁|취침|"
    r"식전|식후|공복|"
    r"필요\s*시|매일|격일|"
    r"식사\s*전|식사\s*후"
    r")",
    re.IGNORECASE,
)

# 글자 높이를 기준으로 같은 행인지 판단합니다.
_ROW_Y_TOLERANCE_HEIGHT_MULTIPLIER = 0.75
_MEDICATION_NAME_CONTINUATION_HEIGHT_MULTIPLIER = 2.5

# 현재 열 구분 비율에서 용법 헤더가 위치하는 지점입니다.
# 전체 이미지에서 가장 오른쪽 글자를 사용하지 않기 위한 기준입니다.
_HEADER_POSITION_RATIOS = {
    "name": 0.235,
    "dose": 0.410,
    "frequency": 0.565,
    "duration": 0.710,
    "timing": 0.900,
}
# 실제 헤더 좌표 기반 열 경계 함수 추가
_COLUMN_ORDER = (
    "name",
    "dose",
    "frequency",
    "duration",
    "timing",
)

_HEADER_ALIASES = {
    "name": {
        "명칭",
        "약품명",
        "약품 명",
        "의약품명",
        "의약품 명",
        "처방약",
    },
    "dose": {
        "투여량",
        "1회 투여량",
        "1회투여량",
        "복용량",
        "1회 복용량",
        "용량",
    },
    "frequency": {
        "투여횟수",
        "투여 횟수",
        "1일 투여횟수",
        "1일투여횟수",
        "복용횟수",
        "복용 횟수",
        "횟수",
    },
    "duration": {
        "투여기간",
        "투여 기간",
        "투약일수",
        "투약 일수",
        "복용기간",
        "복용 기간",
        "일수",
    },
    "timing": {
        "용법",
        "복용방법",
        "복용 방법",
        "복용시간",
        "복용 시간",
        "투여방법",
        "투여 방법",
    },
}

# 행 전체가 안내문인지 판단하는 표현입니다.
# 이 목록 하나로 판정하지 않고 표 위치·첫 열·행 구조와 함께 사용합니다.
_GUIDE_TEXT_PATTERN = re.compile(
    r"(?:"
    r"주의\s*사항?|안내\s*사항?|복약\s*안내|참고\s*사항?|"
    r"보관\s*방법|특이\s*사항?|"
    r"하십시오|하세요|마십시오|하지\s*마|바랍니다|"
    r"문의하|상담하|알려주|관찰하|확인하|"
    r"임의로|지시\s*없이|증상이|이상반응|"
    r"중단하|변경하|조절하|조정하|복용량을\s*조정|"
    r"복용\s*후\s*관찰"
    r")",
    re.IGNORECASE,
)

# 이름 셀 자체가 약품명이 아니라 안내문의 제목·서술어인 경우입니다.
_NON_MEDICATION_NAME_PATTERN = re.compile(
    r"(?:"
    r"주의\s*사항?|안내\s*사항?|복약\s*안내|참고\s*사항?|"
    r"보관\s*방법|특이\s*사항?|"
    r"복용량을\s*조정|용량을\s*조정|"
    r"복용\s*후\s*관찰|주의|관찰|문의|상담|"
    r"하십시오|하세요|마십시오|바랍니다|됩니다|있습니다"
    r")",
    re.IGNORECASE,
)

# 수정: 이름만 인식된 약품은 제품명처럼 이어진 문자열과
# 명확한 제형으로 구성된 경우에만 미확인 약품으로 보존합니다.
# "하루 한 정", "건강 관리정" 같은 문장은 공백 구조 때문에 제외됩니다.
_MEDICATION_NAME_TOKEN = r"[가-힣A-Za-z0-9·ㆍ.+/%()\-]+"

_STANDALONE_MEDICATION_NAME_PATTERN = re.compile(
    rf"^{_MEDICATION_NAME_TOKEN}\s*"
    r"(?:"
    r"연질캡슐|경질캡슐|캡슐|"
    r"주사액|점안액|현탁액|"
    r"정|시럽|액|산|과립|연고|크림|겔|패치|주사"
    r")"
    rf"(?:\s*{_STRENGTH_TEXT})?"
    r"$",
    re.IGNORECASE,
)

_SECTION_END_VALUES = {
    "복약안내",
    "주의사항",
    "환자안내",
    "보관방법",
    "특이사항",
    "기타사항",
    "지도",
    "조제",
    "조제약사",
    "의사서명",
}


class PrescriptionOcrStructurer:
    def __init__(
        self,
        normalizer: MedicationNameNormalizer | None = None,
    ) -> None:
        self._normalizer = normalizer if normalizer is not None else MedicationNameNormalizer()

    def structure(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> list[RecognizedField]:
        structured_fields: list[RecognizedField] = []

        prescribed_date = self._extract_prescribed_date(raw_fields)

        if prescribed_date is not None:
            structured_fields.append(prescribed_date)

        headers = self._find_header_fields(raw_fields)

        if not headers:
            return structured_fields

        medication_rows = self._extract_medication_rows(
            raw_fields,
            headers=headers,
        )

        for medication_index, row in enumerate(
            medication_rows,
            start=1,
        ):
            structured_fields.extend(
                self._structure_medication_row(
                    medication_index=medication_index,
                    row=row,
                    headers=headers,
                )
            )

        return structured_fields

    def _extract_prescribed_date(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> RecognizedField | None:
        for field in raw_fields:
            if _DATE_PATTERN.fullmatch(field.raw_value):
                return RecognizedField(
                    medication_index=0,
                    field_type="PRESCRIBED_DATE",
                    raw_value=field.raw_value,
                    confidence_score=field.confidence_score,
                )
        return None

    # 명칭, 투여량, 투여횟수만 인식되고 용법이 누락돼도 정상적인 표 헤더로 처리
    def _normalize_header_text(self, value: str) -> str:
        return re.sub(r"[\s:·ㆍ|/]+", "", value).lower()

    def _header_kind(self, value: str) -> str | None:
        normalized = self._normalize_header_text(value)

        for kind, aliases in _HEADER_ALIASES.items():
            if any(self._normalize_header_text(alias) == normalized for alias in aliases):
                return kind

        return None

    def _is_section_end_value(
        self,
        value: str,
    ) -> bool:
        normalized = re.sub(
            r"[\s:·ㆍ|/]+",
            "",
            value,
        ).lower()

        return normalized in _SECTION_END_VALUES

    def _group_header_lines(
        self,
        fields: list[RawRecognizedField],
        *,
        tolerance: float,
    ) -> list[list[RawRecognizedField]]:
        header_lines: list[list[RawRecognizedField]] = []

        for field in sorted(
            fields,
            key=lambda item: (item.center_y, item.center_x),
        ):
            matched_line: list[RawRecognizedField] | None = None

            for line in header_lines:
                line_y = self._row_center_y(line)

                if abs(field.center_y - line_y) <= tolerance:
                    matched_line = line
                    break

            if matched_line is None:
                header_lines.append([field])
            else:
                matched_line.append(field)

        return header_lines

    def _find_header_fields(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> dict[str, RawRecognizedField]:
        recognized_headers = [field for field in raw_fields if self._header_kind(field.raw_value) is not None]

        if len(recognized_headers) < 2:
            return {}

        tolerance = self._median_field_height(recognized_headers) * _ROW_Y_TOLERANCE_HEIGHT_MULTIPLIER

        header_lines = self._group_header_lines(
            recognized_headers,
            tolerance=tolerance,
        )

        best: dict[str, RawRecognizedField] = {}

        for line in header_lines:
            by_kind: dict[str, RawRecognizedField] = {}

            for field in sorted(line, key=lambda item: item.center_x):
                kind = self._header_kind(field.raw_value)

                if kind is not None and kind not in by_kind:
                    by_kind[kind] = field

            if len(by_kind) > len(best):
                best = by_kind

        # 명칭 헤더는 필수가 아닙니다.
        # 서로 다른 복약 표 헤더가 최소 2개 있으면 표 후보로 처리합니다.
        if len(best) < 2:
            return {}

        return best

    def _group_medication_candidates(
        self,
        candidates: list[RawRecognizedField],
        *,
        row_y_tolerance: float,
    ) -> list[list[RawRecognizedField]]:
        grouped_rows: list[list[RawRecognizedField]] = []

        for field in candidates:
            if not grouped_rows:
                grouped_rows.append([field])
                continue

            current_row = grouped_rows[-1]
            current_y = self._row_center_y(current_row)

            if abs(field.center_y - current_y) <= row_y_tolerance:
                current_row.append(field)
            else:
                grouped_rows.append([field])

        return grouped_rows

    def _is_primary_medication_row(
        self,
        *,
        row: list[RawRecognizedField],
        headers: dict[str, RawRecognizedField],
    ) -> bool:
        if self._is_medication_name_continuation(
            row=row,
            headers=headers,
        ):
            return False

        return self._is_medication_row(
            row=row,
            headers=headers,
        ) or self._is_standalone_medication_name_row(
            row=row,
            headers=headers,
        )

    def _extract_medication_rows(
        self,
        raw_fields: list[RawRecognizedField],
        *,
        headers: dict[str, RawRecognizedField],
    ) -> list[list[RawRecognizedField]]:
        if not headers:
            return []

        header_fields = list(headers.values())
        header_y = median(field.center_y for field in header_fields)

        table_bounds = self._table_bounds(headers)

        if table_bounds is None:
            return []

        table_left_x, table_right_x = table_bounds
        table_width = table_right_x - table_left_x
        horizontal_margin = table_width * 0.05
        field_height = self._median_field_height(raw_fields)

        row_y_tolerance = field_height * _ROW_Y_TOLERANCE_HEIGHT_MULTIPLIER
        continuation_y_gap = field_height * _MEDICATION_NAME_CONTINUATION_HEIGHT_MULTIPLIER

        section_end_candidates = [
            field.center_y
            for field in raw_fields
            if (field.center_y > header_y and self._is_section_end_value(field.raw_value))
        ]
        section_end_y = min(section_end_candidates) if section_end_candidates else float("inf")

        candidates = [
            field
            for field in raw_fields
            if (
                field.center_y > header_y + row_y_tolerance
                and field.center_y < section_end_y
                and table_left_x - horizontal_margin <= field.center_x <= table_right_x + horizontal_margin
            )
        ]
        candidates.sort(
            key=lambda field: (
                field.center_y,
                field.center_x,
            )
        )

        grouped_rows = self._group_medication_candidates(
            candidates,
            row_y_tolerance=row_y_tolerance,
        )

        medication_row_indexes = {
            index
            for index, row in enumerate(grouped_rows)
            if self._is_primary_medication_row(
                row=row,
                headers=headers,
            )
        }

        medication_rows = {index: list(grouped_rows[index]) for index in medication_row_indexes}

        last_medication_index: int | None = None

        for index, row in enumerate(grouped_rows):
            if index in medication_row_indexes:
                last_medication_index = index
                continue

            if last_medication_index is None:
                continue

            if not self._is_medication_name_continuation(
                row=row,
                headers=headers,
            ):
                last_medication_index = None
                continue

            medication_row = medication_rows[last_medication_index]

            vertical_gap = min(field.center_y for field in row) - max(field.center_y for field in medication_row)

            if not 0 < vertical_gap <= continuation_y_gap:
                last_medication_index = None
                continue

            medication_row.extend(row)

        return [medication_rows[index] for index in sorted(medication_rows)]

    def _is_plausible_medication_name(
        self,
        name_text: str,
    ) -> bool:
        name_text = name_text.strip()

        if len(re.sub(r"\s+", "", name_text)) < 2:
            return False

        if not re.search(r"[가-힣A-Za-z]", name_text):
            return False

        if _NON_MEDICATION_NAME_PATTERN.search(name_text):
            return False

        if _DOSE_INSTRUCTION_PATTERN.fullmatch(name_text):
            return False

        if re.match(r"^\s*\d+\s*[.)]", name_text):
            return False

        if re.fullmatch(r"[\d\s.,/%]+", name_text):
            return False

        return True

    def _has_strong_medication_name_evidence(
        self,
        name_text: str,
    ) -> bool:
        return any(
            (
                _DOSAGE_FORM_PATTERN.search(name_text) is not None,
                _STRENGTH_PATTERN.search(name_text) is not None,
            )
        )

    def _is_medication_row(
        self,
        *,
        row: list[RawRecognizedField],
        headers: dict[str, RawRecognizedField],
    ) -> bool:
        columns = self._split_columns(
            row=row,
            headers=headers,
        )

        if not columns["name"]:
            return False

        name_text = self._join_values(columns["name"])

        if not self._is_plausible_medication_name(name_text):
            return False

        row_text = self._join_values(
            sorted(
                row,
                key=lambda field: field.center_x,
            )
        )

        if _GUIDE_TEXT_PATTERN.search(row_text):
            return False

        dose_text = self._join_values(columns["dose"])
        frequency_text = self._join_values(columns["frequency"])
        duration_text = self._join_values(columns["duration"])
        timing_text = self._join_values(columns["timing"])

        dose_match = DOSE_PATTERN.search(dose_text)
        frequency_match = _FREQUENCY_PATTERN.search(frequency_text)
        duration_match = _DURATION_PATTERN.search(duration_text)
        timing_match = _TIMING_PATTERN.search(timing_text)

        strong_name_evidence = self._has_strong_medication_name_evidence(name_text)

        # 수정: 용법(timing)은 약품 행을 뒷받침하는 보조 정보로만 사용합니다.
        # 안내문에 아침·저녁 등이 있다는 이유만으로 약품 행이 되면 안 됩니다.
        medication_support_count = sum(
            match is not None
            for match in (
                dose_match,
                frequency_match,
                duration_match,
            )
        )

        parsed_support_count = medication_support_count + (1 if timing_match is not None else 0)

        # 수정: 제형이나 함량이 포함되어 있어도 일반 행으로 인정하려면
        # 투여량·횟수·기간 중 하나 이상의 처방 구조 근거가 필요합니다.
        # 이름만 인식된 약품은 아래의 standalone 판정에서 별도로 보존합니다.
        if strong_name_evidence:
            return medication_support_count >= 1

        # 수정: 약품명 근거가 약한 행은 투여량·횟수·기간 중 하나 이상과
        # 전체적으로 두 개 이상의 구조적 근거가 있어야 합니다.
        return medication_support_count >= 1 and parsed_support_count >= 2

    def _is_medication_name_continuation(
        self,
        *,
        row: list[RawRecognizedField],
        headers: dict[str, RawRecognizedField],
    ) -> bool:
        columns = self._split_columns(
            row=row,
            headers=headers,
        )

        # 모든 이름 전용 행을 자동 병합하지 않습니다.
        if not columns["name"]:
            return False

        if any(
            columns[column_name]
            for column_name in (
                "dose",
                "frequency",
                "duration",
                "timing",
            )
        ):
            return False

        name_text = self._join_values(columns["name"])

        return _PACKAGE_CONTINUATION_PATTERN.fullmatch(name_text) is not None

    def _is_standalone_medication_name_row(
        self,
        *,
        row: list[RawRecognizedField],
        headers: dict[str, RawRecognizedField],
    ) -> bool:
        columns = self._split_columns(
            row=row,
            headers=headers,
        )

        if not columns["name"]:
            return False

        if any(
            columns[column_name]
            for column_name in (
                "dose",
                "frequency",
                "duration",
                "timing",
            )
        ):
            return False

        name_text = self._join_values(columns["name"])

        if not self._is_plausible_medication_name(name_text):
            return False

        if _GUIDE_TEXT_PATTERN.search(name_text):
            return False

        if _PACKAGE_CONTINUATION_PATTERN.fullmatch(name_text):
            return False

        # 수정: 문장 일부가 아니라 약품명 전체가 제품명 패턴과 일치해야 합니다.
        return _STANDALONE_MEDICATION_NAME_PATTERN.fullmatch(name_text) is not None

    def _column_centers(
        self,
        headers: dict[str, RawRecognizedField],
    ) -> dict[str, float]:
        observed = [
            (
                _HEADER_POSITION_RATIOS[kind],
                field.center_x,
            )
            for kind, field in headers.items()
        ]

        if len(observed) < 2:
            return {}

        expected_mean = sum(item[0] for item in observed) / len(observed)
        actual_mean = sum(item[1] for item in observed) / len(observed)

        denominator = sum((expected - expected_mean) ** 2 for expected, _ in observed)

        if denominator == 0:
            return {}

        scale = sum((expected - expected_mean) * (actual - actual_mean) for expected, actual in observed) / denominator

        if scale <= 0:
            return {}

        offset = actual_mean - scale * expected_mean

        return {kind: offset + scale * _HEADER_POSITION_RATIOS[kind] for kind in _COLUMN_ORDER}

    def _column_boundaries(
        self,
        headers: dict[str, RawRecognizedField],
    ) -> dict[str, tuple[float, float]]:
        centers = self._column_centers(headers)

        if not centers:
            return {}

        ordered_centers = [centers[kind] for kind in _COLUMN_ORDER]

        internal_boundaries = [
            (left + right) / 2
            for left, right in zip(
                ordered_centers,
                ordered_centers[1:],
                strict=False,
            )
        ]

        first_kind = _COLUMN_ORDER[0]
        last_kind = _COLUMN_ORDER[-1]

        ratio_span = _HEADER_POSITION_RATIOS[last_kind] - _HEADER_POSITION_RATIOS[first_kind]
        center_span = centers[last_kind] - centers[first_kind]
        table_scale = center_span / ratio_span

        outer_left = centers[first_kind] - table_scale * _HEADER_POSITION_RATIOS[first_kind]
        outer_right = centers[last_kind] + table_scale * (1.0 - _HEADER_POSITION_RATIOS[last_kind])

        edges = [
            outer_left,
            *internal_boundaries,
            outer_right,
        ]

        return {kind: (edges[index], edges[index + 1]) for index, kind in enumerate(_COLUMN_ORDER)}

    def _table_bounds(
        self,
        headers: dict[str, RawRecognizedField],
    ) -> tuple[float, float] | None:
        boundaries = self._column_boundaries(headers)

        if not boundaries:
            return None

        return (
            boundaries["name"][0],
            boundaries["timing"][1],
        )

    def _median_field_height(
        self,
        fields: list[RawRecognizedField],
    ) -> float:
        heights = [field.height for field in fields if field.height > 0]

        if not heights:
            return 20.0

        return median(heights)

    def _row_center_y(
        self,
        row: list[RawRecognizedField],
    ) -> float:
        return sum(field.center_y for field in row) / len(row)

    def _split_medication_name_and_strength(
        self,
        value: str,
    ) -> tuple[str, str | None]:
        """
        약품명 끝의 제품 함량만 MEDICATION_STRENGTH로 분리합니다.

        함량 앞에 실제 약품명이 없는 경우에는 원문 전체를 약품명으로
        유지하여 숫자·단위만 약품명으로 오인되는 것을 방지합니다.
        """
        match = _TRAILING_STRENGTH_PATTERN.search(value)

        if match is None:
            return value, None

        medication_name = value[: match.start()].strip()

        if not medication_name:
            return value, None

        # 괄호는 제품 함량 값 자체가 아니므로 제거하고,
        # 괄호 안의 OCR 함량 표기를 보존합니다.
        strength_text = (match.group("parenthesized_strength") or match.group("plain_strength")).strip()

        return medication_name, strength_text

    def _structure_medication_row(
        self,
        *,
        medication_index: int,
        row: list[RawRecognizedField],
        headers: dict[str, RawRecognizedField],
    ) -> list[RecognizedField]:
        columns = self._split_columns(
            row=row,
            headers=headers,
        )
        result: list[RecognizedField] = []

        name_fields = columns["name"]

        if name_fields:
            combined_name = self._join_values(name_fields)

            # 규칙 기반 구조화에서도 LLM 경로와 동일하게
            # 처방 약품명과 제품 함량을 별도 필드로 저장합니다.
            raw_name, strength_text = self._split_medication_name_and_strength(
                combined_name,
            )
            normalized = self._normalizer.normalize(raw_name)
            confidence_score = self._minimum_confidence(name_fields)

            result.append(
                RecognizedField(
                    medication_index=medication_index,
                    field_type="MEDICATION_NAME",
                    raw_value=raw_name,
                    normalized_value=normalized.normalized_value,
                    normalization_version=normalized.normalization_version,
                    confidence_score=confidence_score,
                )
            )

            if strength_text is not None:
                # 제품 함량은 원문 표기를 보존합니다.
                # normalized_value와 normalization_version은 생성하지 않습니다.
                result.append(
                    RecognizedField(
                        medication_index=medication_index,
                        field_type="MEDICATION_STRENGTH",
                        raw_value=strength_text,
                        confidence_score=confidence_score,
                    )
                )

        dose_fields = columns["dose"]
        dose_text = self._join_values(dose_fields)
        dose_match = DOSE_PATTERN.search(dose_text)

        if dose_match is not None:
            result.append(
                RecognizedField(
                    medication_index=medication_index,
                    field_type="DOSE_VALUE",
                    raw_value=dose_match.group("value"),
                    confidence_score=self._minimum_confidence(dose_fields),
                )
            )
            result.append(
                RecognizedField(
                    medication_index=medication_index,
                    field_type="DOSE_UNIT",
                    raw_value=dose_match.group("unit"),
                    confidence_score=self._minimum_confidence(dose_fields),
                )
            )

        frequency_fields = columns["frequency"]
        frequency_text = self._join_values(frequency_fields)
        frequency_match = _FREQUENCY_PATTERN.search(frequency_text)

        if frequency_match is not None:
            result.append(
                RecognizedField(
                    medication_index=medication_index,
                    field_type="FREQUENCY_PER_DAY",
                    raw_value=frequency_match.group("value"),
                    confidence_score=self._minimum_confidence(frequency_fields),
                )
            )

        duration_fields = columns["duration"]
        duration_text = self._join_values(duration_fields)
        duration_match = _DURATION_PATTERN.search(duration_text)

        if duration_match is not None:
            result.append(
                RecognizedField(
                    medication_index=medication_index,
                    field_type="DURATION_DAYS",
                    raw_value=duration_match.group("value"),
                    confidence_score=self._minimum_confidence(duration_fields),
                )
            )
        # timing 열의 임의 안내 문구를 TIMING 필드로 저장하지 않습니다.
        timing_fields = columns["timing"]
        timing_text = self._join_values(timing_fields)

        if timing_fields and _TIMING_PATTERN.search(timing_text) is not None:
            result.append(
                self._recognized_field(
                    medication_index=medication_index,
                    field_type="TIMING",
                    source_fields=timing_fields,
                )
            )

        return result

    def _split_columns(
        self,
        *,
        row: list[RawRecognizedField],
        headers: dict[str, RawRecognizedField],
    ) -> dict[str, list[RawRecognizedField]]:
        columns: dict[str, list[RawRecognizedField]] = {kind: [] for kind in _COLUMN_ORDER}

        boundaries = self._column_boundaries(headers)

        if not boundaries:
            return columns

        for field in row:
            for kind in _COLUMN_ORDER:
                left, right = boundaries[kind]

                if left <= field.center_x < right:
                    columns[kind].append(field)
                    break

        for column_name, fields in columns.items():
            row_y_tolerance = self._median_field_height(fields) * _ROW_Y_TOLERANCE_HEIGHT_MULTIPLIER

            columns[column_name] = self._sort_reading_order(
                fields,
                row_y_tolerance=row_y_tolerance,
            )

        return columns

    def _sort_reading_order(
        self,
        fields: list[RawRecognizedField],
        *,
        row_y_tolerance: float,
    ) -> list[RawRecognizedField]:
        fields_by_y = sorted(
            fields,
            key=lambda field: (
                field.center_y,
                field.center_x,
            ),
        )

        lines: list[list[RawRecognizedField]] = []

        for field in fields_by_y:
            if not lines:
                lines.append([field])
                continue

            current_line = lines[-1]
            current_line_y = self._row_center_y(current_line)

            if abs(field.center_y - current_line_y) <= row_y_tolerance:
                current_line.append(field)
            else:
                lines.append([field])

        ordered_fields: list[RawRecognizedField] = []

        for line in lines:
            ordered_fields.extend(
                sorted(
                    line,
                    key=lambda field: field.center_x,
                )
            )

        return ordered_fields

    def _recognized_field(
        self,
        *,
        medication_index: int,
        field_type: str,
        source_fields: list[RawRecognizedField],
    ) -> RecognizedField:
        return RecognizedField(
            medication_index=medication_index,
            field_type=field_type,
            raw_value=self._join_values(source_fields),
            confidence_score=self._minimum_confidence(source_fields),
        )

    def _join_values(
        self,
        fields: list[RawRecognizedField],
    ) -> str:
        return " ".join(field.raw_value for field in fields)

    def _minimum_confidence(
        self,
        fields: list[RawRecognizedField],
    ) -> float | None:
        confidence_values = [field.confidence_score for field in fields if field.confidence_score is not None]

        if not confidence_values:
            return None

        return min(confidence_values)
