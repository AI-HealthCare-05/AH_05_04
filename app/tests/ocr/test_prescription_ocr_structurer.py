from app.services.ocr_engine import RawRecognizedField
from app.services.prescription_ocr_structurer import (
    PrescriptionOcrStructurer,
)


def _raw_field(
    raw_value: str,
    center_x: float,
    center_y: float,
    confidence_score: float = 0.99,
) -> RawRecognizedField:
    return RawRecognizedField(
        raw_value=raw_value,
        confidence_score=confidence_score,
        center_x=center_x,
        center_y=center_y,
    )


def test_structure_extracts_date_and_two_medication_rows() -> None:
    raw_fields = [
        # 처방일자
        _raw_field(
            "2026-08-12",
            338,
            399,
            0.9998913,
        ),
        # 의약품 표 헤더
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 첫 번째 약품 행
        _raw_field(
            "로수바스타틴정",
            137,
            637,
            0.9955043,
        ),
        _raw_field(
            "10mg",
            247,
            639,
            0.99976605,
        ),
        _raw_field("1정", 394, 639, 0.99593216),
        _raw_field("1회", 547, 638, 0.998978),
        _raw_field("30일", 719, 637, 0.99966156),
        _raw_field("저녁", 893, 641, 0.9965467),
        _raw_field("식후", 942, 641, 0.9965467),
        # 워터마크 토큰: 약품 행으로 인식하면 안 됨
        _raw_field("실제", 615, 660, 0.9986602),
        # 두 번째 약품 행
        _raw_field(
            "에제티미브정",
            136,
            701,
            0.9983261,
        ),
        _raw_field(
            "10mg",
            241,
            703,
            0.99973136,
        ),
        _raw_field("1정", 394, 701, 0.9939819),
        _raw_field("1회", 547, 701, 0.99908113),
        _raw_field("30일", 719, 701, 0.9996865),
        _raw_field("저녁", 893, 701, 0.9962134),
        _raw_field("식후", 941, 701, 0.9962134),
        # 약품 표 밖의 워터마크
        _raw_field("합성", 320, 858, 0.9991572),
        _raw_field("테스트용", 139, 975, 0.9603272),
        # 다음 섹션 시작
        _raw_field("지도", 132, 1042, 0.9974318),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_identity = {(field.medication_index, field.field_type): field for field in result}

    assert len(result) == 13

    prescribed_date = fields_by_identity[(0, "PRESCRIBED_DATE")]
    assert prescribed_date.raw_value == "2026-08-12"
    assert prescribed_date.confidence_score == 0.9998913

    first_name = fields_by_identity[(1, "MEDICATION_NAME")]
    assert first_name.raw_value == "로수바스타틴정 10mg"
    assert first_name.confidence_score == 0.9955043
    assert first_name.normalized_value == "로수바스타틴정 10mg"
    assert first_name.normalization_version == "rule-v1"

    assert fields_by_identity[(1, "DOSE_VALUE")].raw_value == "1"
    assert fields_by_identity[(1, "DOSE_UNIT")].raw_value == "정"
    assert fields_by_identity[(1, "FREQUENCY_PER_DAY")].raw_value == "1"
    assert fields_by_identity[(1, "DURATION_DAYS")].raw_value == "30"
    assert fields_by_identity[(1, "TIMING")].raw_value == "저녁 식후"

    second_name = fields_by_identity[(2, "MEDICATION_NAME")]
    assert second_name.raw_value == "에제티미브정 10mg"

    assert fields_by_identity[(2, "DOSE_VALUE")].raw_value == "1"
    assert fields_by_identity[(2, "DOSE_UNIT")].raw_value == "정"
    assert fields_by_identity[(2, "FREQUENCY_PER_DAY")].raw_value == "1"
    assert fields_by_identity[(2, "DURATION_DAYS")].raw_value == "30"
    assert fields_by_identity[(2, "TIMING")].raw_value == "저녁 식후"

    assert all(field.raw_value not in {"실제", "합성", "테스트용"} for field in result)


def test_structure_does_not_invent_medication_without_table() -> None:
    raw_fields = [
        _raw_field(
            "2026-08-12",
            338,
            399,
            0.99,
        ),
        _raw_field("임의텍스트", 100, 500),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    assert len(result) == 1
    assert result[0].field_type == "PRESCRIBED_DATE"
    assert result[0].raw_value == "2026-08-12"


def test_structure_normalizes_medication_name() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field(
            "로수바스타틴칼숨정",
            137,
            637,
        ),
        _raw_field(
            "10 mg",
            247,
            639,
        ),
        _raw_field("1정", 394, 639),
        _raw_field("1회", 547, 638),
        _raw_field("30일", 719, 637),
        _raw_field("저녁", 893, 641),
        _raw_field("식후", 942, 641),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_name = next(field for field in result if field.field_type == "MEDICATION_NAME")

    assert medication_name.raw_value == ("로수바스타틴칼숨정 10 mg")
    assert medication_name.normalized_value == ("로수바스타틴칼숨정 10mg")
    assert medication_name.normalization_version == ("rule-v1")
