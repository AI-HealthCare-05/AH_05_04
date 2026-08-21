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


def test_structure_excludes_medication_guide_rows() -> None:
    raw_fields = [
        # 의약품 표 헤더
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 실제 약품 1
        _raw_field("로수바스타틴칼숨정", 137, 637),
        _raw_field("10 mg", 247, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        # 실제 약품 2
        _raw_field("에제티미브정", 137, 701),
        _raw_field("(10mg)", 247, 701),
        _raw_field("1정", 394, 701),
        _raw_field("1회", 547, 701),
        _raw_field("30일", 719, 701),
        _raw_field("저녁 식후", 911, 701),
        # 실제 약품 3
        _raw_field("리나글립틴정", 137, 765),
        _raw_field("5 MG", 247, 765),
        _raw_field("1정", 394, 765),
        _raw_field("1회", 547, 765),
        _raw_field("30일", 719, 765),
        _raw_field("아침 식후", 911, 765),
        # 복약 안내 문장
        # 여러 열에 걸쳐 있지만 약품 행은 아닙니다.
        _raw_field("1. 의사의", 120, 900),
        _raw_field("지시 없이", 390, 900),
        _raw_field("임의로 약을", 550, 900),
        _raw_field("중단하지 마십시오.", 900, 900),
        _raw_field("2. 정해진", 120, 940),
        _raw_field("용법과 용량을", 390, 940),
        _raw_field("지켜", 550, 940),
        _raw_field("복용하십시오.", 900, 940),
        _raw_field("복약 안내", 110, 980),
        _raw_field("3. 가벼운", 230, 980),
        _raw_field("근육통이나", 390, 980),
        _raw_field("소화불량이", 550, 980),
        _raw_field("지속되면 문의하십시오.", 900, 980),
        _raw_field("4. 다른 약을", 120, 1020),
        _raw_field("복용 중이거나", 390, 1020),
        _raw_field("새로운 약을", 550, 1020),
        _raw_field("추가하면 알려주십시오.", 900, 1020),
        _raw_field("5. 정기적으로", 120, 1060),
        _raw_field("진료를 받고", 390, 1060),
        _raw_field("검사를 통해", 550, 1060),
        _raw_field("경과를 확인하십시오.", 900, 1060),
        # 다음 섹션
        _raw_field("조제", 132, 1120),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field for field in result if field.field_type == "MEDICATION_NAME"]

    assert len(medication_names) == 3

    assert [field.medication_index for field in medication_names] == [1, 2, 3]

    assert [field.raw_value for field in medication_names] == [
        "로수바스타틴칼숨정 10 mg",
        "에제티미브정 (10mg)",
        "리나글립틴정 5 MG",
    ]

    assert all("복약 안내" not in (field.raw_value or "") for field in medication_names)

    fields_by_identity = {(field.medication_index, field.field_type): field for field in result}

    expected_timings = {
        1: "저녁 식후",
        2: "저녁 식후",
        3: "아침 식후",
    }

    for medication_index in (1, 2, 3):
        assert fields_by_identity[(medication_index, "DOSE_VALUE")].raw_value == "1"
        assert fields_by_identity[(medication_index, "DOSE_UNIT")].raw_value == "정"
        assert fields_by_identity[(medication_index, "FREQUENCY_PER_DAY")].raw_value == "1"
        assert fields_by_identity[(medication_index, "DURATION_DAYS")].raw_value == "30"
        assert fields_by_identity[(medication_index, "TIMING")].raw_value == expected_timings[medication_index]


def test_structure_merges_multiline_medication_name() -> None:
    raw_fields = [
        # 의약품 표 헤더
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 여러 줄로 인식된 실제 약품 행
        _raw_field(
            "오메가-3-산에틸에스테르",
            137,
            637,
        ),
        _raw_field("2캡슐", 394, 650),
        _raw_field("2회", 547, 650),
        _raw_field("30일", 719, 650),
        _raw_field("아침 · 저녁 식후", 911, 650),
        # 같은 약품명의 두 번째 줄
        _raw_field(
            "90연질캡슐 1000mg",
            137,
            675,
        ),
        # 다음 섹션
        _raw_field("조제", 132, 900),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == ("오메가-3-산에틸에스테르 90연질캡슐 1000mg")
    assert fields_by_type["DOSE_VALUE"].raw_value == "2"
    assert fields_by_type["DOSE_UNIT"].raw_value == "캡슐"
    assert fields_by_type["FREQUENCY_PER_DAY"].raw_value == "2"
    assert fields_by_type["DURATION_DAYS"].raw_value == "30"
    assert fields_by_type["TIMING"].raw_value == ("아침 · 저녁 식후")
