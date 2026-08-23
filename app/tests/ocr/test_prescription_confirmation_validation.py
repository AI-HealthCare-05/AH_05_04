from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.models.ocr import ExtractedField, FieldType
from app.services.prescriptions import PrescriptionService


def _field(medication_index: int, field_type: FieldType, confirmed_value: str | None) -> ExtractedField:
    return ExtractedField(
        id=uuid4(),
        ocr_job_id=uuid4(),
        medication_index=medication_index,
        field_type=field_type,
        confirmed_value=confirmed_value,
    )


def _valid_medication_fields(index: int) -> list[ExtractedField]:
    return [
        _field(index, FieldType.MEDICATION_NAME, f"약품{index}"),
        _field(index, FieldType.DOSE_VALUE, "1.5"),
        _field(index, FieldType.DOSE_UNIT, "정"),
        _field(index, FieldType.FREQUENCY_PER_DAY, "2"),
        _field(index, FieldType.TIMING, "식후"),
        _field(index, FieldType.DURATION_DAYS, "7"),
    ]


def _valid_prescribed_date() -> ExtractedField:
    return _field(0, FieldType.PRESCRIBED_DATE, "2026-08-01")


def test_build_confirmed_data_accepts_fully_confirmed_medication() -> None:
    fields = [_valid_prescribed_date(), *_valid_medication_fields(1)]

    prescribed_date, medications = PrescriptionService._build_confirmed_data(fields)

    assert prescribed_date.isoformat() == "2026-08-01"
    assert medications == [
        {
            "medication_name": "약품1",
            "dose_value": 1.5,
            "dose_unit": "정",
            "frequency_per_day": 2,
            "timing_text": "식후",
            "duration_days": 7,
            "display_order": 1,
        }
    ]


def test_build_confirmed_data_allows_missing_optional_fields() -> None:
    fields = [
        _valid_prescribed_date(),
        _field(1, FieldType.MEDICATION_NAME, "약품1"),
        _field(1, FieldType.DOSE_VALUE, "1"),
        _field(1, FieldType.FREQUENCY_PER_DAY, "1"),
        _field(1, FieldType.DURATION_DAYS, "3"),
    ]

    _, medications = PrescriptionService._build_confirmed_data(fields)

    assert medications[0]["dose_unit"] is None
    assert medications[0]["timing_text"] is None


def test_build_confirmed_data_rejects_when_no_medications_present() -> None:
    fields = [_valid_prescribed_date()]

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "PRESCRIPTION_REQUIRED_FIELD_MISSING"


def test_build_confirmed_data_rejects_unconfirmed_medication_name() -> None:
    fields = [_valid_prescribed_date(), *_valid_medication_fields(1)]
    fields = [f for f in fields if f.field_type != FieldType.MEDICATION_NAME]

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    error = exc_info.value
    assert error.status_code == 422
    assert error.code == "PRESCRIPTION_REQUIRED_FIELD_MISSING"
    assert any(detail.field == "medications[1].medication_name" for detail in error.details)


@pytest.mark.parametrize(
    "missing_field_type",
    [FieldType.DOSE_VALUE, FieldType.FREQUENCY_PER_DAY, FieldType.DURATION_DAYS],
)
def test_build_confirmed_data_rejects_missing_required_numeric_field(missing_field_type: FieldType) -> None:
    fields = [_valid_prescribed_date(), *_valid_medication_fields(1)]
    fields = [f for f in fields if f.field_type != missing_field_type]

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    assert exc_info.value.code == "PRESCRIPTION_REQUIRED_FIELD_MISSING"


def test_build_confirmed_data_rejects_invalid_dose_value_format_instead_of_storing_null() -> None:
    fields = [_valid_prescribed_date(), *_valid_medication_fields(1)]
    for field in fields:
        if field.field_type == FieldType.DOSE_VALUE:
            field.confirmed_value = "약 반 알"

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    error = exc_info.value
    assert error.status_code == 422
    assert error.code == "VALIDATION_FAILED"
    assert any(
        detail.field == "medications[1].dose_value" and detail.rejected_value == "약 반 알" for detail in error.details
    )


def test_build_confirmed_data_rejects_missing_prescribed_date() -> None:
    fields = _valid_medication_fields(1)

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    error = exc_info.value
    assert error.code == "PRESCRIPTION_REQUIRED_FIELD_MISSING"
    assert any(detail.field == "prescribed_date" for detail in error.details)


def test_build_confirmed_data_rejects_invalid_prescribed_date_format() -> None:
    fields = [_field(0, FieldType.PRESCRIBED_DATE, "2026/08/01"), *_valid_medication_fields(1)]

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    error = exc_info.value
    assert error.code == "VALIDATION_FAILED"
    assert any(detail.field == "prescribed_date" for detail in error.details)


def test_build_confirmed_data_reports_missing_fields_across_multiple_medications() -> None:
    fields = [
        _valid_prescribed_date(),
        *_valid_medication_fields(1),
        *[f for f in _valid_medication_fields(2) if f.field_type != FieldType.DURATION_DAYS],
    ]

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    error = exc_info.value
    assert error.code == "PRESCRIPTION_REQUIRED_FIELD_MISSING"
    assert any(detail.field == "medications[2].duration_days" for detail in error.details)


def test_build_confirmed_data_rejects_missing_medication_index() -> None:
    fields = [_valid_prescribed_date(), *_valid_medication_fields(1), *_valid_medication_fields(3)]

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    error = exc_info.value
    assert error.code == "PRESCRIPTION_REQUIRED_FIELD_MISSING"
    assert any(detail.field == "medications[2]" for detail in error.details)


@pytest.mark.parametrize("invalid_value", ["1.5", "0", "-1"])
def test_build_confirmed_data_rejects_invalid_integer_format(invalid_value: str) -> None:
    fields = [_valid_prescribed_date(), *_valid_medication_fields(1)]
    for field in fields:
        if field.field_type == FieldType.FREQUENCY_PER_DAY:
            field.confirmed_value = invalid_value

    with pytest.raises(ApiError) as exc_info:
        PrescriptionService._build_confirmed_data(fields)

    assert exc_info.value.code == "VALIDATION_FAILED"
