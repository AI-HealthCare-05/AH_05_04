import pytest

from app.services.guide_ai.exceptions import GuideGenerationSafetyError
from app.services.guide_ai.schemas import GeneratedGuideDraft, GeneratedMedicationGuidance
from app.services.guide_ai.validators import validate_generated_draft


def _draft(
    guidance: str = "처방 지시를 따라 복용해 주세요.", notice: str = "궁금한 점은 약사에게 확인해 주세요."
) -> GeneratedGuideDraft:
    return GeneratedGuideDraft(
        medications=[GeneratedMedicationGuidance(source_index=0, guidance=guidance)],
        general_notice=notice,
    )


def test_validator_accepts_exactly_one_guidance_per_source_index() -> None:
    validate_generated_draft(_draft(), medication_count=1)


def test_validator_does_not_extract_dose_across_korean_word_boundaries() -> None:
    validate_generated_draft(
        _draft(notice="복약에 대한 불명확한 정보는 의료진이나 약사에게 확인하시기 바랍니다."),
        medication_count=1,
    )


@pytest.mark.parametrize(
    "indexes",
    [[0, 0], [0, 2], [1]],
)
def test_validator_rejects_duplicate_missing_or_unknown_source_indexes(indexes: list[int]) -> None:
    draft = GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(source_index=index, guidance="지시를 확인해 주세요.") for index in indexes
        ],
        general_notice="궁금한 점은 약사에게 확인해 주세요.",
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_generated_draft(draft, medication_count=2)

    assert exc_info.value.rule_id == "PRESCRIPTION_MISMATCH"


@pytest.mark.parametrize(
    "text",
    [
        "5mg씩 드세요.",
        "두 정 복용하세요.",
        "두정 복용하세요.",
        "하루 3회 복용하세요.",
        "7일 복용하세요.",
        "한 알 더 드세요.",
        "한알 더 드세요.",
        "한알씩 복용하세요.",
        "두정씩 복용하세요.",
        "두번씩 복용하세요.",
        "열두정 복용하세요.",
        "스무알 복용하세요.",
        "스물한알 복용하세요.",
        "백정 복용하세요.",
        "반알 복용하세요.",
        "일 정 복용하세요.",
        "한 정 복용하세요.",
        "한 번 복용하세요.",
        "이 번 복용하세요.",
        "두정씩은 복용하세요.",
        "한 번만 복용하세요.",
        "3일간 복용하세요.",
        "5mg을 복용하세요.",
        "두 알을 복용하세요.",
    ],
)
def test_validator_rejects_new_prescription_numbers(text: str) -> None:
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_generated_draft(_draft(guidance=text), medication_count=1)

    assert exc_info.value.rule_id == "RX_NUMERIC_IN_AI_TEXT"


@pytest.mark.parametrize(
    "text",
    [
        "일반적인 복약 안내를 확인하세요.",
        "약을 한꺼번에 복용하지 마세요.",
        "반드시 처방 지시를 확인하세요.",
        "복용 일정 확인이 필요합니다.",
        "한정 안내를 확인하세요.",
        "이번 안내를 확인하세요.",
        "한번 확인해보세요.",
        "1회용 포장입니다.",
    ],
)
def test_validator_allows_korean_words_that_are_not_prescription_quantities(text: str) -> None:
    validate_generated_draft(_draft(guidance=text), medication_count=1)


@pytest.mark.parametrize(
    "text", ["복용을 중단하세요.", "용량을 줄여 드세요.", "약을 끊어도 됩니다.", "복용 횟수 변경을 권합니다."]
)
def test_validator_rejects_prescription_change_directives(text: str) -> None:
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_generated_draft(_draft(guidance=text), medication_count=1)

    assert exc_info.value.rule_id == "RX_CHANGE_DIRECTIVE"


def test_validator_allows_explicit_negative_change_guidance() -> None:
    validate_generated_draft(_draft(guidance="복용을 임의로 중단하지 마세요."), medication_count=1)


def test_validator_rejects_positive_directive_mixed_with_safe_negation() -> None:
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_generated_draft(_draft(guidance="복용을 중단하지 말고 용량을 줄여 드세요."), medication_count=1)

    assert exc_info.value.rule_id == "RX_CHANGE_DIRECTIVE"


def test_validator_rejects_unrecognized_directive_mixed_with_safe_negation() -> None:
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_generated_draft(_draft(guidance="복용을 중단하지 말고 용량을 줄이십시오."), medication_count=1)

    assert exc_info.value.rule_id == "RX_CHANGE_DIRECTIVE"


@pytest.mark.parametrize(
    "text",
    [
        "이 약은 통증을 치료합니다.",
        "부작용으로 두통이 생깁니다.",
        "다른 약과 상호작용이 없습니다.",
        "이 약은 혈압을 조절합니다.",
    ],
)
def test_validator_rejects_unsupported_medical_claims(text: str) -> None:
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_generated_draft(_draft(guidance=text), medication_count=1)

    assert exc_info.value.rule_id == "RX_MEDICAL_CLAIM"


@pytest.mark.parametrize(
    "text", ["<b>복용</b>", "[안내](https://example.test)", "https://example.test", "안내\u200b문"]
)
def test_validator_rejects_markup_urls_and_unsafe_unicode(text: str) -> None:
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_generated_draft(_draft(guidance=text), medication_count=1)

    assert exc_info.value.rule_id == "UNSAFE_MARKUP"
