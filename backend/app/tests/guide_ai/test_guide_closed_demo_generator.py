from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from app.core.guide_closed_demo_retrieval import GuideClosedDemoEvidence
from app.services.guide_ai.closed_demo_generator import (
    CLOSED_DEMO_GUIDE_PROMPT_VERSION,
    ClosedDemoGuidanceField,
    ClosedDemoGuideDraft,
    ClosedDemoMedicationGuidance,
    GuideClosedDemoGenerator,
    render_closed_demo_plaintext_guide,
    validate_closed_demo_draft,
)
from app.services.guide_ai.exceptions import GuideGenerationSafetyError
from app.services.guide_ai.schemas import GuideGenerationInput, MedicationInput
from app.services.guide_ai.validators import (
    RULE_CHANGE_DIRECTIVE,
    RULE_PRESCRIPTION_MISMATCH,
    RULE_UNSAFE_MARKUP,
)


def _make_evidence(slot: int) -> GuideClosedDemoEvidence:
    return GuideClosedDemoEvidence(
        slot=slot,
        external_document_id=f"mfds-label:200610660:item{slot}",
        source_code="MFDS_LABEL",
        source_version="1.0",
        locator="dosage",
        content=SensitiveText("safe text"),
    )


def test_render_closed_demo_plaintext_guide_emits_6_sections() -> None:
    guide_input = GuideGenerationInput(
        medications=[
            MedicationInput(
                medication_name="노바스크정",
                strength_text="5mg",
                dose_value=Decimal("1"),
                dose_unit="정",
                frequency_per_day=1,
                timing_text="아침 식후",
                duration_days=30,
            )
        ]
    )
    draft = ClosedDemoGuideDraft(
        medications=[
            ClosedDemoMedicationGuidance(
                source_index=0,
                medication_caution=ClosedDemoGuidanceField(text="매일 일정한 시간에 복용하세요.", evidence_slots=[1]),
                food_and_drink=ClosedDemoGuidanceField(text="자몽주스와 함께 드시지 마세요.", evidence_slots=[1]),
                alcohol_and_smoking=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                possible_discomfort=ClosedDemoGuidanceField(
                    text="초기에 가벼운 두통이나 어지러움이 있을 수 있어요.", evidence_slots=[2]
                ),
                seek_medical_care=ClosedDemoGuidanceField(
                    text="발목이나 다리에 심한 부종이 생기면 병원에 방문하세요.", evidence_slots=[2]
                ),
                pregnancy_and_breastfeeding=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
            )
        ],
        general_notice="처방받은 약의 복용 일정과 횟수를 잘 지켜주세요.",
    )

    rendered = render_closed_demo_plaintext_guide(guide_input, draft)

    assert "복약 가이드" in rendered
    assert "[1] 노바스크정 5mg" in rendered
    assert "1회량: 1 정" in rendered
    assert "하루 횟수: 하루 1회" in rendered
    assert "복용 시점: 아침 식후" in rendered
    assert "복용 기간: 30일" in rendered
    assert "복용 시 주의해야 할 점: 매일 일정한 시간에 복용하세요." in rendered
    assert "주의해야 할 음식·음료: 자몽주스와 함께 드시지 마세요." in rendered
    assert "나타날 수 있는 불편감: 초기에 가벼운 두통이나 어지러움이 있을 수 있어요." in rendered
    assert "이런 증상은 병원에 가세요: 발목이나 다리에 심한 부종이 생기면 병원에 방문하세요." in rendered
    # alcohol_and_smoking and pregnancy_and_breastfeeding are null, so their headers should NOT be present
    assert "음주/흡연 안내" not in rendered
    assert "임신·수유 중 안내" not in rendered
    assert "공통 안내: 처방받은 약의 복용 일정과 횟수를 잘 지켜주세요." in rendered
    assert "안전 안내: 임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요." in rendered


def test_validate_closed_demo_draft_rejects_slot_mismatch() -> None:
    evidences = {0: (_make_evidence(1), _make_evidence(2))}
    draft = ClosedDemoGuideDraft(
        medications=[
            ClosedDemoMedicationGuidance(
                source_index=0,
                medication_caution=ClosedDemoGuidanceField(
                    text="주의사항", evidence_slots=[99]
                ),  # slot 99 does not exist
                food_and_drink=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                alcohol_and_smoking=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                possible_discomfort=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                seek_medical_care=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                pregnancy_and_breastfeeding=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
            )
        ],
        general_notice="공통 안내 문장입니다.",
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == "EVIDENCE_SLOT_MISMATCH"


def test_validate_closed_demo_draft_rejects_change_directive() -> None:
    evidences = {0: (_make_evidence(1),)}
    draft = ClosedDemoGuideDraft(
        medications=[
            ClosedDemoMedicationGuidance(
                source_index=0,
                medication_caution=ClosedDemoGuidanceField(
                    text="혈압이 떨어지면 복용을 중단하세요.", evidence_slots=[1]
                ),
                food_and_drink=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                alcohol_and_smoking=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                possible_discomfort=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                seek_medical_care=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                pregnancy_and_breastfeeding=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
            )
        ],
        general_notice="공통 안내 문장입니다.",
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_CHANGE_DIRECTIVE


def test_validate_closed_demo_draft_rejects_unsafe_markup() -> None:
    evidences = {0: (_make_evidence(1),)}
    draft = ClosedDemoGuideDraft(
        medications=[
            ClosedDemoMedicationGuidance(
                source_index=0,
                medication_caution=ClosedDemoGuidanceField(
                    text="자세한 내용은 <script>alert(1)</script> 참조", evidence_slots=[1]
                ),
                food_and_drink=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                alcohol_and_smoking=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                possible_discomfort=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                seek_medical_care=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                pregnancy_and_breastfeeding=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
            )
        ],
        general_notice="공통 안내 문장입니다.",
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_UNSAFE_MARKUP


def test_validate_closed_demo_draft_rejects_prescription_mismatch() -> None:
    evidences = {0: (_make_evidence(1),)}
    draft = ClosedDemoGuideDraft(
        medications=[],  # 0 medications instead of expected 1
        general_notice="공통 안내 문장입니다.",
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_PRESCRIPTION_MISMATCH


@pytest.mark.asyncio
async def test_generator_generate_full_cycle() -> None:
    mock_retriever = MagicMock()
    mock_retriever.retrieve_exact_evidence = AsyncMock(return_value=(_make_evidence(1), _make_evidence(2)))

    draft = ClosedDemoGuideDraft(
        medications=[
            ClosedDemoMedicationGuidance(
                source_index=0,
                medication_caution=ClosedDemoGuidanceField(text="정해진 시간에 복용하세요.", evidence_slots=[1]),
                food_and_drink=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                alcohol_and_smoking=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                possible_discomfort=ClosedDemoGuidanceField(
                    text="가벼운 두통이 발생할 수 있습니다.", evidence_slots=[2]
                ),
                seek_medical_care=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                pregnancy_and_breastfeeding=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
            )
        ],
        general_notice="복약 일정을 꾸준히 유지해 주세요.",
    )

    mock_parsed = MagicMock(
        output=[MagicMock(content=[MagicMock(parsed=draft)])],
        model="gpt-4o",
    )
    mock_client = MagicMock()
    mock_client.responses.parse = AsyncMock(return_value=mock_parsed)

    generator = GuideClosedDemoGenerator(
        client=mock_client,
        model="gpt-4o",
        timeout_seconds=20.0,
        retriever=mock_retriever,
    )

    guide_input = GuideGenerationInput(
        medications=[
            MedicationInput(
                medication_name="노바스크정",
                strength_text="5mg",
                dose_value=Decimal("1"),
                dose_unit="정",
                frequency_per_day=1,
                timing_text="아침 식후",
                duration_days=14,
            )
        ]
    )

    result = await generator.generate(guide_input, medication_item_seqs={0: "200610660"})

    assert result.prompt_version == CLOSED_DEMO_GUIDE_PROMPT_VERSION
    assert result.model_name == "gpt-4o"
    assert "복용 시 주의해야 할 점: 정해진 시간에 복용하세요." in result.content
    assert "나타날 수 있는 불편감: 가벼운 두통이 발생할 수 있습니다." in result.content
    assert "공통 안내: 복약 일정을 꾸준히 유지해 주세요." in result.content
