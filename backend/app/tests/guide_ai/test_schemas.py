from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.guide_ai.schemas import (
    GeneratedGuideDraft,
    GeneratedMedicationGuidance,
    GuideGenerationInput,
    GuideGuidanceIntent,
    MedicationInput,
)


def test_medication_input_normalizes_display_text_and_preserves_decimal_precision() -> None:
    medication = MedicationInput(
        medication_name="  A\u030a약   정  ",
        dose_value=Decimal("0.500"),
        dose_unit="  mg ",
        frequency_per_day=2,
        timing_text="  아침   식후 ",
        duration_days=7,
    )

    assert medication.medication_name == "Å약 정"
    assert medication.dose_value == Decimal("0.500")
    assert medication.dose_unit == "mg"
    assert medication.timing_text == "아침 식후"


@pytest.mark.parametrize("field,value", [("dose_value", "0"), ("frequency_per_day", 0), ("duration_days", -1)])
def test_medication_input_rejects_non_positive_numbers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        MedicationInput.model_validate({"medication_name": "합성약", field: value})


@pytest.mark.parametrize("value", ["", "   ", "합성\x00약", "합성\u202e약", "합성\u200b약"])
def test_medication_input_rejects_invalid_medication_names(value: str) -> None:
    with pytest.raises(ValidationError):
        MedicationInput(medication_name=value)


def test_guide_input_requires_at_least_one_medication_and_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        GuideGenerationInput(medications=[])

    with pytest.raises(ValidationError):
        MedicationInput.model_validate({"medication_name": "합성약", "patient_id": "patient-1"})


def test_generated_draft_strips_text_and_forbids_unknown_fields() -> None:
    draft = GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(
                source_index=0,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                guidance="  처방 지시를 따라 복용해 주세요.  ",
            )
        ],
        general_notice="  궁금한 점은 의료진에게 확인해 주세요.  ",
    )

    assert draft.medications[0].guidance == "처방 지시를 따라 복용해 주세요."
    assert draft.general_notice == "궁금한 점은 의료진에게 확인해 주세요."

    with pytest.raises(ValidationError):
        GeneratedGuideDraft.model_validate(
            {
                "medications": [
                    {
                        "source_index": 0,
                        "guidance_intent": GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                        "guidance": "안내",
                        "medication_name": "조작된 약명",
                    }
                ],
                "general_notice": "안내",
            }
        )


def test_generated_draft_normalizes_unicode_and_trim_without_hiding_internal_characters() -> None:
    draft = GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(
                source_index=0,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                guidance="  A\u030a약  안내\t문  ",
            )
        ],
        general_notice="  공통   안내  ",
    )

    assert draft.medications[0].guidance == "Å약  안내\t문"
    assert draft.general_notice == "공통   안내"


def test_generated_text_length_limits_apply_after_display_normalization() -> None:
    draft = GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(
                source_index=0,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                guidance=f"  {'가' * 150}  ",
            )
        ],
        general_notice=f"  {'나' * 300}  ",
    )

    assert len(draft.medications[0].guidance) == 150
    assert len(draft.general_notice) == 300


def test_generated_text_rejects_non_string_values_as_validation_errors() -> None:
    with pytest.raises(ValidationError):
        GeneratedMedicationGuidance.model_validate(
            {
                "source_index": 0,
                "guidance_intent": GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                "guidance": 123,
            }
        )


def test_generated_output_rejects_coerced_source_indexes() -> None:
    with pytest.raises(ValidationError):
        GeneratedMedicationGuidance.model_validate(
            {
                "source_index": "0",
                "guidance_intent": GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                "guidance": "안내를 확인해 주세요.",
            }
        )


def test_guidance_intent_enum_matches_confirmed_prescription_contract() -> None:
    assert {intent.value for intent in GuideGuidanceIntent} == {
        "FOLLOW_CONFIRMED_TIMING",
        "FOLLOW_CONFIRMED_SCHEDULE",
    }


def test_generated_medication_guidance_requires_strict_intent_echo() -> None:
    guidance = GeneratedMedicationGuidance.model_validate_json(
        '{"source_index":0,"guidance_intent":"FOLLOW_CONFIRMED_TIMING",'
        '"guidance":"안내된 복용 시점을 확인해 그대로 따라 주세요."}'
    )

    assert guidance.guidance_intent.value == "FOLLOW_CONFIRMED_TIMING"

    with pytest.raises(ValidationError):
        GeneratedMedicationGuidance.model_validate(
            {
                "source_index": 0,
                "guidance": "안내된 복용 시점을 확인해 그대로 따라 주세요.",
            }
        )
