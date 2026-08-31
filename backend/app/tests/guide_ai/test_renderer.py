from decimal import Decimal

import pytest

from app.services.guide_ai.exceptions import GuideGenerationInvalidResponseError
from app.services.guide_ai.renderer import render_plaintext_guide
from app.services.guide_ai.schemas import (
    GeneratedGuideDraft,
    GeneratedMedicationGuidance,
    GuideGenerationInput,
    GuideGuidanceIntent,
    MedicationInput,
)


def test_renderer_uses_original_prescription_values_in_input_order() -> None:
    guide_input = GuideGenerationInput(
        medications=[
            MedicationInput(
                medication_name="합성약 A",
                dose_value=Decimal("0.5000"),
                dose_unit="mg",
                frequency_per_day=2,
                timing_text="아침 식후",
                duration_days=7,
            ),
            MedicationInput(medication_name="합성약 B", dose_value=Decimal("1"), frequency_per_day=1),
        ]
    )
    draft = GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(
                source_index=1,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_SCHEDULE,
                guidance="안내된 복용 계획을 확인해 그대로 따라 주세요.",
            ),
            GeneratedMedicationGuidance(
                source_index=0,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_TIMING,
                guidance="안내된 복용 시점을 확인해 그대로 따라 주세요.",
            ),
        ],
        general_notice="불명확한 내용은 의료진 또는 약사에게 확인해 주세요.",
    )

    assert (
        render_plaintext_guide(guide_input, draft)
        == """복약 가이드

[1] 합성약 A
용량: 0.5 mg
복용 횟수: 하루 2회
복용 시점: 아침 식후
복용 기간: 7일
복약 안내: 안내된 복용 시점을 확인해 그대로 따라 주세요.

[2] 합성약 B
용량 정보는 처방전 또는 의료진 안내를 확인해 주세요.
복용 횟수: 하루 1회
복약 안내: 안내된 복용 계획을 확인해 그대로 따라 주세요.

공통 안내: 불명확한 내용은 의료진 또는 약사에게 확인해 주세요.
안전 안내: 임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요."""
    )


def test_renderer_rejects_content_over_maximum_length() -> None:
    guide_input = GuideGenerationInput(
        medications=[MedicationInput(medication_name="가" * 10_000, frequency_per_day=1)]
    )
    draft = GeneratedGuideDraft(
        medications=[
            GeneratedMedicationGuidance(
                source_index=0,
                guidance_intent=GuideGuidanceIntent.FOLLOW_CONFIRMED_SCHEDULE,
                guidance="안내된 복용 계획을 확인해 그대로 따라 주세요.",
            )
        ],
        general_notice="불명확한 내용은 의료진 또는 약사에게 확인해 주세요.",
    )

    with pytest.raises(GuideGenerationInvalidResponseError):
        render_plaintext_guide(guide_input, draft)
