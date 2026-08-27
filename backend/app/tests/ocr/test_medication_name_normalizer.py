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
        (
            "복합정 500 mg / 5 mg",
            "복합정 500mg/5mg",
        ),
        (
            "주사액 1 mg / mL",
            "주사액 1mg/mL",
        ),
        (
            "주사액 1 mg / ML",
            "주사액 1mg/mL",
        ),
        (
            "연고 5 %",
            "연고 5%",
        ),
        (
            "연고 5%",
            "연고 5%",
        ),
        (
            "복합정(500 mg / 5 mg)",
            "복합정 500mg/5mg",
        ),
        (
            "암로디핀5mg/발사르탄80mg정",
            "암로디핀5mg/발사르탄80mg정",
        ),
        (
            "암로디핀5 mg/발사르탄80 MG정",
            "암로디핀5mg/발사르탄80mg정",
        ),
        (
            "암로디핀5 mg발사르탄80 MG정",
            "암로디핀5mg발사르탄80mg정",
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


@pytest.mark.parametrize(
    "raw_value",
    [
        "복합정 500 mg / 5 mg",
        "주사액 1 mg / mL",
        "연고 5 %",
        "연고 5%",
        "복합정(500 mg / 5 mg)",
        "암로디핀5 mg/발사르탄80 MG정",
        "암로디핀5 mg발사르탄80 MG정",
    ],
)
def test_normalization_is_idempotent(
    raw_value: str,
) -> None:
    normalizer = MedicationNameNormalizer()

    normalized = normalizer.normalize(
        raw_value,
    ).normalized_value

    normalized_again = normalizer.normalize(
        normalized,
    ).normalized_value

    assert normalized_again == normalized
