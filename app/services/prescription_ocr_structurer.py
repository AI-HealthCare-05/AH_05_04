import re
from statistics import median

from app.services.ocr_engine import (
    RawRecognizedField,
    RecognizedField,
)

_DATE_PATTERN = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")
DOSE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[^\d\s]+)")
_FREQUENCY_PATTERN = re.compile(r"(\d+)\s*회")
_DURATION_PATTERN = re.compile(r"(\d+)\s*일")

_ROW_Y_TOLERANCE = 15.0

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

        section_end_candidates = [
            field.center_y
            for field in raw_fields
            if (field.raw_value in _SECTION_END_VALUES and field.center_y > header_y)
        ]
        section_end_y = min(section_end_candidates) if section_end_candidates else float("inf")

        candidates = [
            field
            for field in raw_fields
            if (field.center_y > header_y + _ROW_Y_TOLERANCE and field.center_y < section_end_y)
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

            if abs(field.center_y - current_y) <= _ROW_Y_TOLERANCE:
                current_row.append(field)
            else:
                grouped_rows.append([field])

        return [
            row
            for row in grouped_rows
            if self._is_medication_row(
                row=row,
                maximum_x=max(field.center_x for field in raw_fields),
            )
        ]

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

        return bool(columns["name"] and columns["dose"] and columns["frequency"])

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
            result.append(
                self._recognized_field(
                    medication_index=medication_index,
                    field_type="MEDICATION_NAME",
                    source_fields=name_fields,
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

        for fields in columns.values():
            fields.sort(key=lambda field: field.center_x)

        return columns

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
