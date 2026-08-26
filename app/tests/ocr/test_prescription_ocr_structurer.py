import pytest

from app.services.ocr_engine import RawRecognizedField
from app.services.prescription_ocr_structurer import (
    DOSE_PATTERN,
    PrescriptionOcrStructurer,
)


def _raw_field(
    raw_value: str,
    center_x: float,
    center_y: float,
    confidence_score: float = 0.99,
    height: float = 20.0,
) -> RawRecognizedField:
    return RawRecognizedField(
        raw_value=raw_value,
        confidence_score=confidence_score,
        center_x=center_x,
        center_y=center_y,
        height=height,
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


def test_structure_does_not_write_recognized_content_to_stdout_or_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field("합성약정 10mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("저녁 식후", 911, 637),
    ]

    PrescriptionOcrStructurer().structure(raw_fields)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


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


@pytest.mark.parametrize("scale", [0.5, 1.0, 2.0])
def test_structure_merges_multiline_medication_name(
    scale: float,
) -> None:
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
    scaled_fields = [
        RawRecognizedField(
            raw_value=field.raw_value,
            confidence_score=field.confidence_score,
            center_x=field.center_x * scale,
            center_y=field.center_y * scale,
            height=field.height * scale,
        )
        for field in raw_fields
    ]
    result = PrescriptionOcrStructurer().structure(scaled_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == ("오메가-3-산에틸에스테르 90연질캡슐 1000mg")
    assert fields_by_type["DOSE_VALUE"].raw_value == "2"
    assert fields_by_type["DOSE_UNIT"].raw_value == "캡슐"
    assert fields_by_type["FREQUENCY_PER_DAY"].raw_value == "2"
    assert fields_by_type["DURATION_DAYS"].raw_value == "30"
    assert fields_by_type["TIMING"].raw_value == ("아침 · 저녁 식후")


# 투여량 표현 테스트 교체
@pytest.mark.parametrize(
    ("raw_value", "expected_value", "expected_unit"),
    [
        ("5mg", "5", "mg"),
        ("1병", "1", "병"),
        ("1정씩", "1", "정"),
        ("1정 분할", "1", "정"),
        ("2캡슐", "2", "캡슐"),
        ("10mL", "10", "mL"),
    ],
)
def test_dose_pattern_accepts_prescription_units(
    raw_value: str,
    expected_value: str,
    expected_unit: str,
) -> None:
    match = DOSE_PATTERN.search(raw_value)

    assert match is not None
    assert match.group("value") == expected_value
    assert match.group("unit") == expected_unit


@pytest.mark.parametrize(
    "raw_value",
    [
        "1회",
        "30일",
    ],
)
def test_dose_pattern_rejects_frequency_and_duration_units(
    raw_value: str,
) -> None:
    assert DOSE_PATTERN.search(raw_value) is None


def test_structure_excludes_numeric_medication_guide_row() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 실제 약품 행
        _raw_field("로수바스타틴정", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        # 숫자가 포함된 복약 안내 문장
        _raw_field("1. 증상이 계속되면", 137, 701),
        _raw_field("3회", 394, 701),
        _raw_field("확인하고", 547, 701),
        _raw_field("1일", 719, 701),
        _raw_field("의료진에게 문의하세요.", 911, 701),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["로수바스타틴정"]


def test_structure_attaches_continuation_to_preceding_medication_row() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 첫 번째 약품
        _raw_field("오메가-3-산에틸에스테르", 137, 637),
        _raw_field("2캡슐", 394, 637),
        _raw_field("2회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("아침 · 저녁 식후", 911, 637),
        # 첫 번째 약품명의 연속 행
        # 두 번째 약품에 더 가깝지만 바로 앞 약품에 병합되어야 합니다.
        _raw_field("90연질캡슐 1000mg", 137, 675),
        # 두 번째 약품
        _raw_field("에제티미브정 10mg", 137, 701),
        _raw_field("1정", 394, 701),
        _raw_field("1회", 547, 701),
        _raw_field("30일", 719, 701),
        _raw_field("저녁 식후", 911, 701),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field for field in result if field.field_type == "MEDICATION_NAME"]

    assert [field.medication_index for field in medication_names] == [1, 2]
    assert [field.raw_value for field in medication_names] == [
        "오메가-3-산에틸에스테르 90연질캡슐 1000mg",
        "에제티미브정 10mg",
    ]


def test_structure_accepts_dose_with_trailing_ocr_text() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field("분할복용정", 137, 637),
        _raw_field("1정 분할", 394, 637),
        _raw_field("1회", 547, 637),
        # 투여기간은 OCR에서 누락된 상황
        _raw_field("저녁 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == "분할복용정"
    assert fields_by_type["DOSE_VALUE"].raw_value == "1"
    assert fields_by_type["DOSE_UNIT"].raw_value == "정"
    assert fields_by_type["FREQUENCY_PER_DAY"].raw_value == "1"
    assert "DURATION_DAYS" not in fields_by_type


# 약품 행 유지 테스트 추가
@pytest.mark.parametrize(
    ("dose_text", "expected_unit"),
    [
        ("5mg", "mg"),
        ("1병", "병"),
        ("1정씩", "정"),
    ],
)
def test_structure_keeps_medication_with_various_dose_units(
    dose_text: str,
    expected_unit: str,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field("테스트약정", 137, 637),
        _raw_field(dose_text, 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == ("테스트약정")
    assert fields_by_type["DOSE_UNIT"].raw_value == (expected_unit)


# 오른쪽 워터마크 회귀 테스트 추가
def test_structure_is_not_affected_by_right_side_watermark() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field("로수바스타틴정", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        # 처방전 오른쪽의 별도 문장 또는 워터마크
        _raw_field("테스트용 워터마크", 3000, 640),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["로수바스타틴정"]


# 이름만 인식된 두 번째 약품 테스트 추가
def test_structure_does_not_merge_name_only_second_medication() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 첫 번째 약품
        _raw_field("로수바스타틴정", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        # OCR이 이름만 인식한 별개의 두 번째 약품
        _raw_field("에제티미브정 10mg", 137, 675),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field for field in result if field.field_type == "MEDICATION_NAME"]

    assert [field.medication_index for field in medication_names] == [1, 2]

    assert [field.raw_value for field in medication_names] == [
        "로수바스타틴정",
        "에제티미브정 10mg",
    ]

    second_medication_fields = [field.field_type for field in result if field.medication_index == 2]

    assert second_medication_fields == ["MEDICATION_NAME"]


# 1정, 1회가 있는 안내 문장 테스트 추가
def test_structure_excludes_guide_sentence_with_dose_and_frequency() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field("로수바스타틴정", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        # 형식상 약품 행처럼 보이지만 실제로는 안내 문장
        _raw_field("증상이 있으면", 137, 701),
        _raw_field("1정", 394, 701),
        _raw_field("1회", 547, 701),
        _raw_field("임의로 복용하지 마십시오.", 911, 701),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["로수바스타틴정"]


def test_structure_keeps_medication_when_timing_header_is_missing() -> None:
    raw_fields = [
        # 용법 헤더만 OCR에서 누락
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        # 실제 약품
        _raw_field("로수바스타틴정 10mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == ("로수바스타틴정 10mg")
    assert fields_by_type["DOSE_VALUE"].raw_value == "1"
    assert fields_by_type["FREQUENCY_PER_DAY"].raw_value == "1"
    assert fields_by_type["DURATION_DAYS"].raw_value == "30"


def test_structure_keeps_partial_medication_for_confirmation() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        # 횟수와 기간은 OCR에서 누락
        _raw_field("에제티미브정 10mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("저녁 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == ("에제티미브정 10mg")
    assert fields_by_type["DOSE_VALUE"].raw_value == "1"
    assert fields_by_type["DOSE_UNIT"].raw_value == "정"
    assert fields_by_type["TIMING"].raw_value == "저녁 식후"
    assert "FREQUENCY_PER_DAY" not in fields_by_type
    assert "DURATION_DAYS" not in fields_by_type


def test_structure_excludes_precaution_row_with_dose_values() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 실제 약품
        _raw_field("로수바스타틴정", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("저녁 식후", 911, 637),
        # 숫자가 포함됐지만 실제로는 안내문
        _raw_field("주의 사항", 137, 701),
        _raw_field("1정", 394, 701),
        _raw_field("1회", 547, 701),
        _raw_field("복용 후 관찰", 911, 701),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["로수바스타틴정"]


def test_structure_excludes_action_sentence_ending_in_jeong() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        # 마지막 글자가 '정'이지만 약품명이 아님
        _raw_field("복용량을 조정", 137, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == []


def test_structure_keeps_name_only_medication_as_unconfirmed() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        # 나머지 열 인식 실패
        _raw_field("리나글립틴정 5mg", 137, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_fields = [field for field in result if field.medication_index == 1]

    assert [field.field_type for field in medication_fields] == ["MEDICATION_NAME"]

    assert medication_fields[0].raw_value == ("리나글립틴정 5mg")


@pytest.mark.parametrize(
    "missing_header",
    [
        "투여량",
        "투여횟수",
        "용법",
    ],
)
def test_structure_does_not_depend_on_three_header_count(
    missing_header: str,
) -> None:
    header_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
    ]

    raw_fields = [field for field in header_fields if field.raw_value != missing_header] + [
        _raw_field("암로디핀정 5mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("아침 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["암로디핀정 5mg"]


# 정상 약품이 사라지는 회귀 방지
def test_structure_does_not_stop_on_section_word_inside_timing() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        _raw_field("로수바스타틴정", 137, 637),
        _raw_field("1정", 394, 637),
        # '지도'를 포함하지만 섹션 제목은 아님
        _raw_field(
            "의사 지도하에 저녁 식후",
            911,
            637,
        ),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["로수바스타틴정"]


# 임의의 안내문이 timing 신호가 되지 않는다
def test_structure_does_not_accept_arbitrary_timing_text() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        # 약품명이 아닌 안내 문구
        _raw_field("수분 섭취", 137, 637),
        # 복용 시점 패턴이 아닌 임의 문구
        _raw_field("충분히 유지", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == []


# 회귀 테스트
def test_structure_keeps_medication_when_only_name_and_frequency_headers_exist() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("암로디핀정 5mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("아침 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == "암로디핀정 5mg"
    assert fields_by_type["DOSE_VALUE"].raw_value == "1"
    assert fields_by_type["FREQUENCY_PER_DAY"].raw_value == "1"
    assert fields_by_type["DURATION_DAYS"].raw_value == "30"
    assert fields_by_type["TIMING"].raw_value == "아침 식후"


def _shift_x(
    fields: list[RawRecognizedField],
    offset: float,
) -> list[RawRecognizedField]:
    return [
        RawRecognizedField(
            raw_value=field.raw_value,
            confidence_score=field.confidence_score,
            center_x=field.center_x + offset,
            center_y=field.center_y,
            height=field.height,
        )
        for field in fields
    ]


def test_structure_keeps_medication_when_name_header_is_missing() -> None:
    raw_fields = [
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("투여기간", 719, 581),
        _raw_field("용법", 911, 581),
        _raw_field("암로디핀정 5mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("아침 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == ("암로디핀정 5mg")
    assert fields_by_type["DOSE_VALUE"].raw_value == "1"
    assert fields_by_type["FREQUENCY_PER_DAY"].raw_value == "1"
    assert fields_by_type["DURATION_DAYS"].raw_value == "30"


@pytest.mark.parametrize("offset", [-180, -80, 80, 220])
def test_structure_is_invariant_to_horizontal_translation(
    offset: float,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("투여기간", 719, 581),
        _raw_field("용법", 911, 581),
        _raw_field("암로디핀정 5mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("30일", 719, 637),
        _raw_field("아침 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    baseline = PrescriptionOcrStructurer().structure(raw_fields)
    shifted = PrescriptionOcrStructurer().structure(_shift_x(raw_fields, offset))

    baseline_values = [
        (
            field.medication_index,
            field.field_type,
            field.raw_value,
        )
        for field in baseline
    ]
    shifted_values = [
        (
            field.medication_index,
            field.field_type,
            field.raw_value,
        )
        for field in shifted
    ]

    assert shifted_values == baseline_values


@pytest.mark.parametrize(
    ("name", "dose", "frequency", "timing"),
    [
        ("충분한 물 섭취", "1컵", "", "매일"),
        ("운전 금지", "1시간", "", "취침 전"),
    ],
)
def test_structure_excludes_guide_rows_without_medication_evidence(
    name: str,
    dose: str,
    frequency: str,
    timing: str,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field("암로디핀정 5mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        _raw_field("아침 식후", 911, 637),
        _raw_field(name, 137, 701),
        _raw_field(dose, 394, 701),
        *([_raw_field(frequency, 547, 701)] if frequency else []),
        _raw_field(timing, 911, 701),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["암로디핀정 5mg"]


@pytest.mark.parametrize(
    ("name", "timing"),
    [
        ("식사 관리", "아침 저녁"),
        ("수분 섭취", "매일"),
        ("혈압 관리", "아침 식후"),
    ],
)
def test_structure_excludes_guide_row_with_only_timing(
    name: str,
    timing: str,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        # 수정: 약품명이 아닌 안내문과 시간 정보만 있는 행입니다.
        _raw_field(name, 137, 637),
        _raw_field(timing, 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == []


@pytest.mark.parametrize(
    ("guide_name", "timing"),
    [
        ("비타민 500mg", "매일"),
        ("건강 관리정", "아침 식후"),
    ],
)
def test_structure_excludes_strong_name_guide_row_without_medication_support(
    guide_name: str,
    timing: str,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("투여기간", 719, 581),
        _raw_field("용법", 911, 581),
        # 수정: 함량·제형 표현이 있어도 투여 구조가 없는 안내 행입니다.
        _raw_field(guide_name, 137, 637),
        _raw_field(timing, 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == []


@pytest.mark.parametrize(
    "guide_name",
    [
        "하루 한 정",
        "건강 관리정",
    ],
)
def test_structure_excludes_sentence_like_standalone_medication_name(
    guide_name: str,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        # 수정: 문장 끝에 제형처럼 보이는 글자가 있어도 약품으로 만들지 않습니다.
        _raw_field(guide_name, 137, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == []


def test_structure_does_not_merge_dose_instruction_into_name() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        _raw_field("오메가-3-산에틸에스테르", 137, 637),
        _raw_field("1캡슐", 394, 637),
        _raw_field("저녁 식후", 911, 637),
        _raw_field("1정 복용", 137, 675),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["오메가-3-산에틸에스테르"]


def test_structure_merges_package_continuation_with_strength() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        _raw_field(
            "오메가-3-산에틸에스테르",
            137,
            637,
        ),
        _raw_field("1캡슐", 394, 637),
        _raw_field("저녁 식후", 911, 637),
        _raw_field(
            "90연질캡슐 1000mg",
            137,
            675,
        ),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    assert medication_names == ["오메가-3-산에틸에스테르 90연질캡슐 1000mg"]


@pytest.mark.parametrize(
    ("continuation", "expected_name"),
    [
        (
            "1000mg",
            "오메가-3-산에틸에스테르 1000mg",
        ),
        (
            "정 500mg",
            "오메가-3-산에틸에스테르 정 500mg",
        ),
        (
            "정500mg",
            "오메가-3-산에틸에스테르 정500mg",
        ),
        (
            "90연질캡슐1000mg",
            "오메가-3-산에틸에스테르 90연질캡슐1000mg",
        ),
    ],
)
def test_structure_merges_additional_medication_name_continuation(
    continuation: str,
    expected_name: str,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("용법", 911, 581),
        # 첫 번째 줄의 실제 약품명입니다.
        _raw_field(
            "오메가-3-산에틸에스테르",
            137,
            637,
        ),
        _raw_field("1캡슐", 394, 637),
        _raw_field("저녁 식후", 911, 637),
        # 수정: OCR이 별도 줄로 인식한 함량 또는 제형+함량입니다.
        _raw_field(
            continuation,
            137,
            675,
        ),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    medication_names = [field.raw_value for field in result if field.field_type == "MEDICATION_NAME"]

    # 수정: 연속 행이 별도 약품으로 분리되지 않고 직전 약품에 병합됩니다.
    assert medication_names == [expected_name]


@pytest.mark.parametrize(
    "invalid_dose",
    [
        "3회분",
        "30일치",
        "1복용",
        "1시간",
        "1컵",
    ],
)
def test_dose_pattern_rejects_non_dose_units(
    invalid_dose: str,
) -> None:
    assert DOSE_PATTERN.fullmatch(invalid_dose) is None


@pytest.mark.parametrize(
    "invalid_dose",
    [
        "1정분할",
        "1회분",
        "1일분",
    ],
)
def test_dose_pattern_does_not_extract_attached_instruction_as_unit(
    invalid_dose: str,
) -> None:
    # 수정: 실제 구조화 로직이 search()를 사용하므로 부분 매칭도 없어야 합니다.
    assert DOSE_PATTERN.search(invalid_dose) is None


@pytest.mark.parametrize(
    ("duration_text", "expected_days"),
    [
        ("30일", "30"),
        ("30일간", "30"),
        ("30일분", "30"),
        ("7일분", "7"),
        ("30일치", "30"),
    ],
)
def test_structure_extracts_duration_variants(
    duration_text: str,
    expected_days: str,
) -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("투여기간", 719, 581),
        _raw_field("용법", 911, 581),
        _raw_field("에제티미브정 10mg", 137, 637),
        _raw_field("1정", 394, 637),
        _raw_field("1회", 547, 637),
        # 수정: 일·일간·일분·일치 기간 표현을 실제 구조화로 검증합니다.
        _raw_field(duration_text, 719, 637),
        _raw_field("저녁 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["DURATION_DAYS"].raw_value == expected_days


@pytest.mark.parametrize(
    "valid_dose",
    [
        "1정",
        "1병",
        "5mg",
        "0.5mL",
        "I정",
    ],
)
def test_dose_pattern_keeps_valid_or_reviewable_doses(
    valid_dose: str,
) -> None:
    assert DOSE_PATTERN.fullmatch(valid_dose) is not None


def test_structure_keeps_row_with_ocr_digit_confusion() -> None:
    raw_fields = [
        _raw_field("명칭", 237, 581),
        _raw_field("투여량", 413, 581),
        _raw_field("투여횟수", 570, 581),
        _raw_field("용법", 911, 581),
        _raw_field("리나글립틴정 5mg", 137, 637),
        _raw_field("I정", 394, 637),
        _raw_field("I회", 547, 637),
        _raw_field("아침 식후", 911, 637),
        _raw_field("조제", 132, 800),
    ]

    result = PrescriptionOcrStructurer().structure(raw_fields)

    fields_by_type = {field.field_type: field for field in result if field.medication_index == 1}

    assert fields_by_type["MEDICATION_NAME"].raw_value == ("리나글립틴정 5mg")
    assert fields_by_type["DOSE_VALUE"].raw_value == "I"
    assert fields_by_type["DOSE_UNIT"].raw_value == "정"
    assert fields_by_type["FREQUENCY_PER_DAY"].raw_value == "I"
