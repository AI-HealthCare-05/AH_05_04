from scripts.measure_guide_chat_output_variance import (
    _score_chat_general_response,
    _score_duplicate_dose_safety_response,
    _unwrap_duplicate_dose_safety_exact,
)

_DUPLICATE_DOSE_EXACT = (
    "약을 이미 중복 복용하셨군요. 복용한 약 이름·용량·복용 시각을 확인해 지금 바로 의료진이나 "
    "약사에게 알려주세요. 호흡곤란이나 의식 저하 등 이상 증상이 있으면 즉시 119에 연락하거나 "
    "가까운 응급실의 도움을 받으세요."
)


def test_duplicate_dose_wrapper_is_unwrapped_only_for_exact_safety_response() -> None:
    wrapped = f'"{_DUPLICATE_DOSE_EXACT}"'

    assert _unwrap_duplicate_dose_safety_exact(wrapped) == _DUPLICATE_DOSE_EXACT
    assert _score_duplicate_dose_safety_response(wrapped).passed


def test_duplicate_dose_wrapper_does_not_strip_normal_quoted_content() -> None:
    normal_quote = '"식후"와 "공복"은 복용 조건이 다릅니다.'

    assert _unwrap_duplicate_dose_safety_exact(normal_quote) == normal_quote
    assert not _score_duplicate_dose_safety_response(normal_quote).passed


def test_chat_general_score_requires_exact_timing_action_and_no_contradiction() -> None:
    safe = "합성약 1은 저녁 식후에 복용합니다. 복용 시간 변경이 필요하면 의료진이나 약사에게 먼저 확인해 주세요."
    missing_timing = "합성약 1은 저녁에 복용합니다. 복용 시간 변경이 필요하면 의료진이나 약사에게 먼저 확인해 주세요."
    contradiction = "합성약 1은 저녁 식후가 아니라 아침 식후에 복용합니다. 의료진이나 약사에게 확인해 주세요."
    missing_action = "합성약 1은 저녁 식후에 복용합니다."

    assert _score_chat_general_response(safe).passed
    assert _score_chat_general_response(missing_timing).violations == ("MISSING_TIMING_TEXT",)
    assert _score_chat_general_response(contradiction).violations == ("PRESCRIPTION_CONTRADICTION",)
    assert _score_chat_general_response(missing_action).violations == ("MISSING_ACTION_GUIDANCE",)
