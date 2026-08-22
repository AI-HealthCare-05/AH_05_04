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
DOSE_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>"
    r"(?!(?:회|일)(?:씩)?(?=\s|$))"
    r"[A-Za-zµμ㎍㎎㎖가-힣]+?"
    r")"
    r"(?:씩)?"
    r"(?=\s|$)",
    re.IGNORECASE,
)

_FREQUENCY_PATTERN = re.compile(r"(\d+)\s*회")
_DURATION_PATTERN = re.compile(r"(\d+)\s*일")
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

_PACKAGE_CONTINUATION_PATTERN = re.compile(
    r"^\d+\s*"
    r"(?:연질|경질)?"
    r"(?:캡슐|정|포|병|앰플|바이알)"
    r"(?![A-Za-z가-힣])"
    r".*$",
    re.IGNORECASE,
)

# 이름만 인식된 약품을 미확인 항목으로 남기기 위한 강한 약품명 형태입니다.
# 단독 확정 기준이 아니라, 약품명 열 내부라는 조건과 함께 사용합니다.
_STANDALONE_MEDICATION_NAME_PATTERN = re.compile(
    r"(?:"
    r"정|캡슐|연질캡슐|경질캡슐|시럽|액|산|과립|"
    r"연고|크림|겔|패치|주사|주사액|점안액|현탁액"
    r")"
    r"(?:\s*\d+(?:\.\d+)?\s*"
    r"(?:mg|g|mcg|µg|μg|mL|%))?"
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

        medication_rows = self._extract_medication_rows(raw_fields)

        for medication_index, row in enumerate(
            medication_rows,
            start=1,
        ):
            structured_fields.extend(
                self._structure_medication_row(
                    medication_index=medication_index,
                    row=row,
                    maximum_x=self._table_right_x(raw_fields),
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

    def _find_header_fields(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> dict[str, RawRecognizedField]:
        recognized_headers = [field for field in raw_fields if self._header_kind(field.raw_value) is not None]

        if not recognized_headers:
            return {}

        tolerance = self._median_field_height(recognized_headers) * _ROW_Y_TOLERANCE_HEIGHT_MULTIPLIER

        # 약품명 헤더가 포함된 실제 헤더 행만 후보로 삼습니다.
        name_headers = [field for field in recognized_headers if self._header_kind(field.raw_value) == "name"]

        best: dict[str, RawRecognizedField] = {}

        for name_header in name_headers:
            same_line = [
                field for field in recognized_headers if abs(field.center_y - name_header.center_y) <= tolerance
            ]

            by_kind: dict[str, RawRecognizedField] = {}
            for field in sorted(
                same_line,
                key=lambda item: item.center_x,
            ):
                kind = self._header_kind(field.raw_value)
                if kind is not None and kind not in by_kind:
                    by_kind[kind] = field

            if len(by_kind) > len(best):
                best = by_kind

        # 헤더 개수 3개가 아니라 약품명 헤더 + 복약 관련 헤더 1개를 요구합니다.
        if "name" not in best or len(best) < 2:
            return {}

        return best

    def _extract_medication_rows(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> list[list[RawRecognizedField]]:
        headers = self._find_header_fields(raw_fields)

        if not headers:
            return []

        header_fields = list(headers.values())
        header_y = median(field.center_y for field in header_fields)

        maximum_x = self._table_right_x(raw_fields)
        field_height = self._median_field_height(raw_fields)

        row_y_tolerance = field_height * _ROW_Y_TOLERANCE_HEIGHT_MULTIPLIER
        continuation_y_gap = field_height * _MEDICATION_NAME_CONTINUATION_HEIGHT_MULTIPLIER

        section_end_candidates = [
            field.center_y
            for field in raw_fields
            if (field.center_y > header_y and self._is_section_end_value(field.raw_value))
        ]
        section_end_y = min(section_end_candidates) if section_end_candidates else float("inf")

        # 오른쪽 멀리 있는 워터마크가 열 구분 및 행 판정에 사용되지 않습니다.
        candidates = [
            field
            for field in raw_fields
            if (
                field.center_y > header_y + row_y_tolerance
                and field.center_y < section_end_y
                and field.center_x <= maximum_x * 1.10
            )
        ]
        candidates.sort(
            key=lambda field: (
                field.center_y,
                field.center_x,
            )
        )

        grouped_rows: list[list[RawRecognizedField]] = []

        for field in candidates:
            if not grouped_rows:
                grouped_rows.append([field])
                continue

            current_row = grouped_rows[-1]
            current_y = sum(item.center_y for item in current_row) / len(current_row)

            if abs(field.center_y - current_y) <= row_y_tolerance:
                current_row.append(field)
            else:
                grouped_rows.append([field])

        # 에제티미브정 10mg처럼 이름만 OCR 된 두 번째 약은 첫 번째 약에 합쳐지지 않고 별도의 미확정 약품으로 남습니다.
        medication_row_indexes = {
            index
            for index, row in enumerate(grouped_rows)
            if (
                self._is_medication_row(
                    row=row,
                    maximum_x=maximum_x,
                )
                or self._is_standalone_medication_name_row(
                    row=row,
                    maximum_x=maximum_x,
                )
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
                maximum_x=maximum_x,
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

        if re.match(r"^\s*\d+\s*[.)]", name_text):
            return False

        if re.fullmatch(r"[\d\s.,/%]+", name_text):
            return False

        return True

    def _is_medication_row(
        self,
        *,
        row: list[RawRecognizedField],
        maximum_x: float,
    ) -> bool:
        columns = self._split_columns(
            row=row,
            maximum_x=maximum_x,
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

        # 명령문·주의문 형태는 숫자가 있더라도 약품으로 만들지 않습니다.
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

        # 횟수·기간이 누락돼도 약품명과 용량 또는
        # 실제 복용 시점이 인식된 행은 보존합니다.
        has_supporting_medication_value = any(
            (
                dose_match is not None,
                frequency_match is not None,
                duration_match is not None,
                timing_match is not None,
            )
        )

        return self._is_plausible_medication_name(name_text) and has_supporting_medication_value

    def _is_medication_name_continuation(
        self,
        *,
        row: list[RawRecognizedField],
        maximum_x: float,
    ) -> bool:
        columns = self._split_columns(
            row=row,
            maximum_x=maximum_x,
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
        maximum_x: float,
    ) -> bool:
        columns = self._split_columns(
            row=row,
            maximum_x=maximum_x,
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

        return _STANDALONE_MEDICATION_NAME_PATTERN.search(name_text) is not None

    def _table_right_x(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> float:
        headers = self._find_header_fields(raw_fields)

        if not headers:
            return max(
                (field.center_x for field in raw_fields),
                default=0.0,
            )

        table_right_candidates = [field.center_x / _HEADER_POSITION_RATIOS[kind] for kind, field in headers.items()]

        return median(table_right_candidates)

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

    def _structure_medication_row(
        self,
        *,
        medication_index: int,
        row: list[RawRecognizedField],
        maximum_x: float,
    ) -> list[RecognizedField]:
        columns = self._split_columns(
            row=row,
            maximum_x=maximum_x,
        )
        result: list[RecognizedField] = []

        name_fields = columns["name"]

        if name_fields:
            raw_name = self._join_values(name_fields)
            normalized = self._normalizer.normalize(raw_name)

            result.append(
                RecognizedField(
                    medication_index=medication_index,
                    field_type="MEDICATION_NAME",
                    raw_value=raw_name,
                    normalized_value=(normalized.normalized_value),
                    normalization_version=(normalized.normalization_version),
                    confidence_score=(self._minimum_confidence(name_fields)),
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
                    raw_value=frequency_match.group(1),
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
                    raw_value=duration_match.group(1),
                    confidence_score=self._minimum_confidence(duration_fields),
                )
            )

        timing_fields = columns["timing"]
        if timing_fields:
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
        maximum_x: float,
    ) -> dict[str, list[RawRecognizedField]]:
        columns: dict[str, list[RawRecognizedField]] = {
            "name": [],
            "dose": [],
            "frequency": [],
            "duration": [],
            "timing": [],
        }
        # 실제로 찾은 헤더 X좌표는 표 폭 계산에만 사용되고, 열 경계에는 사용되지 않습니다.
        for field in row:
            position = field.center_x / maximum_x

            if position < 0.30:
                columns["name"].append(field)
            elif position < 0.48:
                columns["dose"].append(field)
            elif position < 0.64:
                columns["frequency"].append(field)
            elif position < 0.82:
                columns["duration"].append(field)
            else:
                columns["timing"].append(field)

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
