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
_DOSE_UNITS = (
    "캡슐",
    "정",
    "포",
    "mL",
)
_DOSE_UNIT_PATTERN = "|".join(re.escape(unit) for unit in _DOSE_UNITS)

DOSE_PATTERN = re.compile(
    rf"(?P<value>\d+(?:\.\d+)?)\s*"
    rf"(?P<unit>{_DOSE_UNIT_PATTERN})(?![A-Za-z가-힣])",
    re.IGNORECASE,
)
_FREQUENCY_PATTERN = re.compile(r"(\d+)\s*회")
_DURATION_PATTERN = re.compile(r"(\d+)\s*일")

_ROW_Y_TOLERANCE_RATIO = 0.02
_MEDICATION_NAME_CONTINUATION_Y_GAP_RATIO = 0.05

_HEADER_VALUES = {
    "명칭",
    "투여량",
    "투여횟수",
    "용법",
}
_SECTION_END_VALUES = {
    "지도",
    "조제",
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
                    maximum_x=max(field.center_x for field in raw_fields),
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

    def _extract_medication_rows(
        self,
        raw_fields: list[RawRecognizedField],
    ) -> list[list[RawRecognizedField]]:
        header_fields = [field for field in raw_fields if field.raw_value in _HEADER_VALUES]

        if len(header_fields) < 3:
            return []

        header_y = median(field.center_y for field in header_fields)

        maximum_x = max(field.center_x for field in raw_fields)
        row_y_tolerance = maximum_x * _ROW_Y_TOLERANCE_RATIO
        continuation_y_gap = maximum_x * _MEDICATION_NAME_CONTINUATION_Y_GAP_RATIO

        section_end_candidates = [
            field.center_y
            for field in raw_fields
            if (field.raw_value in _SECTION_END_VALUES and field.center_y > header_y)
        ]
        section_end_y = min(section_end_candidates) if section_end_candidates else float("inf")

        candidates = [
            field
            for field in raw_fields
            if (field.center_y > header_y + row_y_tolerance and field.center_y < section_end_y)
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

        medication_row_indexes = {
            index
            for index, row in enumerate(grouped_rows)
            if self._is_medication_row(
                row=row,
                maximum_x=maximum_x,
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

        dose_text = self._join_values(columns["dose"])
        frequency_text = self._join_values(
            columns["frequency"],
        )
        duration_text = self._join_values(
            columns["duration"],
        )

        dose_match = DOSE_PATTERN.search(dose_text)
        frequency_match = _FREQUENCY_PATTERN.search(frequency_text)
        duration_match = _DURATION_PATTERN.search(duration_text)

        # 지원하는 투여량 형식이 존재하고, 투여횟수 또는 투여기간 중
        # 하나 이상이 인식된 경우에만 약품 행으로 처리합니다.
        return dose_match is not None and (frequency_match is not None or duration_match is not None)

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

        return bool(columns["name"]) and not any(
            columns[column_name]
            for column_name in (
                "dose",
                "frequency",
                "duration",
                "timing",
            )
        )

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

        row_y_tolerance = maximum_x * _ROW_Y_TOLERANCE_RATIO

        for column_name, fields in columns.items():
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
