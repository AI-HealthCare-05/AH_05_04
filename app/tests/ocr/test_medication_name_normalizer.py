import pytest

from app.services.medication_name_normalizer import (
    MedicationNameNormalizer,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (
            "로수바스타틴칼숨정  10 mg",
            "로수바스타틴칼숨정 10mg",
        ),
        (
            "에제티미브정(10mg)",
            "에제티미브정 10mg",
        ),
        (
            "리나글립틴정 5 MG",
            "리나글립틴정 5mg",
        ),
        (
            "성분정 100 μg",
            "성분정 100mcg",
        ),
        (
            "주사액 250 ML",
            "주사액 250mL",
        ),
    ],
)
def test_normalizes_medication_name(
    raw_value: str,
    expected: str,
) -> None:
    result = MedicationNameNormalizer().normalize(raw_value)

    assert result.raw_value == raw_value
    assert result.normalized_value == expected
