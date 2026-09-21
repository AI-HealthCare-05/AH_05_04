from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from app.core.guide_closed_demo_retrieval import GuideClosedDemoEvidence
from app.services.guide_ai.closed_demo_generator import (
    CLOSED_DEMO_GENERAL_NOTICE,
    CLOSED_DEMO_GUIDE_PROMPT_VERSION,
    GUIDE_CLOSED_DEMO_SYSTEM_INSTRUCTIONS,
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
    RULE_MEDICAL_CLAIM,
    RULE_NUMERIC_IN_AI_TEXT,
    RULE_PRESCRIPTION_MISMATCH,
    RULE_UNAPPROVED_GENERAL_NOTICE,
    RULE_UNSAFE_MARKUP,
    _validate_text,
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
        general_notice=CLOSED_DEMO_GENERAL_NOTICE,
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
        general_notice=CLOSED_DEMO_GENERAL_NOTICE,
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
        general_notice=CLOSED_DEMO_GENERAL_NOTICE,
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_UNSAFE_MARKUP


def test_validate_closed_demo_draft_rejects_prescription_mismatch() -> None:
    evidences = {0: (_make_evidence(1),)}
    draft = ClosedDemoGuideDraft(
        medications=[],  # 0 medications instead of expected 1
        general_notice=CLOSED_DEMO_GENERAL_NOTICE,
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
        general_notice=CLOSED_DEMO_GENERAL_NOTICE,
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
    assert f"공통 안내: {CLOSED_DEMO_GENERAL_NOTICE}" in result.content


ALL_GUIDANCE_FIELDS = [
    "medication_caution",
    "food_and_drink",
    "alcohol_and_smoking",
    "possible_discomfort",
    "seek_medical_care",
    "pregnancy_and_breastfeeding",
]


@pytest.mark.parametrize("field_name", ALL_GUIDANCE_FIELDS)
def test_validate_closed_demo_draft_rejects_numeric_in_ai_text(field_name: str) -> None:
    evidences = {0: (_make_evidence(1),)}
    field_kwargs = {name: ClosedDemoGuidanceField(text=None, evidence_slots=[]) for name in ALL_GUIDANCE_FIELDS}
    field_kwargs[field_name] = ClosedDemoGuidanceField(
        text="하루에 5mg 이상 드시지 마세요.",
        evidence_slots=[1],
    )
    medication = ClosedDemoMedicationGuidance(source_index=0, **field_kwargs)
    draft = ClosedDemoGuideDraft(
        medications=[medication],
        general_notice=CLOSED_DEMO_GENERAL_NOTICE,
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_NUMERIC_IN_AI_TEXT


@pytest.mark.parametrize("field_name", ALL_GUIDANCE_FIELDS)
def test_validate_closed_demo_draft_rejects_medical_claim(field_name: str) -> None:
    evidences = {0: (_make_evidence(1),)}
    field_kwargs = {name: ClosedDemoGuidanceField(text=None, evidence_slots=[]) for name in ALL_GUIDANCE_FIELDS}
    field_kwargs[field_name] = ClosedDemoGuidanceField(
        text="이 약은 혈압을 낮춥니다.",
        evidence_slots=[1],
    )
    medication = ClosedDemoMedicationGuidance(source_index=0, **field_kwargs)
    draft = ClosedDemoGuideDraft(
        medications=[medication],
        general_notice=CLOSED_DEMO_GENERAL_NOTICE,
    )

    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_MEDICAL_CLAIM


def test_validate_closed_demo_draft_rejects_numeric_in_general_notice() -> None:
    evidences = {0: (_make_evidence(1),)}
    draft = ClosedDemoGuideDraft(
        medications=[
            ClosedDemoMedicationGuidance(
                source_index=0,
                medication_caution=ClosedDemoGuidanceField(text="정해진 시간에 복용하세요.", evidence_slots=[1]),
                food_and_drink=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                alcohol_and_smoking=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                possible_discomfort=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                seek_medical_care=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                pregnancy_and_breastfeeding=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
            )
        ],
        general_notice="하루 1회 1정 복용하세요.",
    )
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_NUMERIC_IN_AI_TEXT


def test_build_provider_input_minimizes_payload() -> None:
    import json

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
    evidences = {0: (_make_evidence(1),)}
    raw_payload = GuideClosedDemoGenerator._build_provider_input(guide_input, evidences)
    data = json.loads(raw_payload)

    assert "medications" in data
    assert len(data["medications"]) == 1
    med_payload = data["medications"][0]

    assert set(med_payload.keys()) == {"source_index", "evidence"}
    assert med_payload["source_index"] == 0
    assert len(med_payload["evidence"]) == 1
    ev_item = med_payload["evidence"][0]
    assert set(ev_item.keys()) == {"slot", "content"}
    assert ev_item["slot"] == 1
    assert ev_item["content"] == "safe text"

    for forbidden in (
        "medication_name",
        "strength_text",
        "dose_value",
        "dose_unit",
        "frequency_per_day",
        "timing_text",
        "duration_days",
        "source_code",
        "source_version",
        "locator",
        "external_document_id",
    ):
        assert forbidden not in med_payload
        assert forbidden not in ev_item
        assert forbidden not in raw_payload


def test_closed_demo_prompt_contains_safety_rules_and_null_fallback() -> None:
    """Regression test verifying GUIDE_CLOSED_DEMO_SYSTEM_INSTRUCTIONS v2 prompt alignment."""
    assert CLOSED_DEMO_GUIDE_PROMPT_VERSION == "guide-closed-demo-rag-v2"
    prompt = GUIDE_CLOSED_DEMO_SYSTEM_INSTRUCTIONS

    # 1. No prescription numbers or units in AI text
    assert "처방 수치/단위를 절대 포함하지 마십시오" in prompt
    for unit in ("mg", "g", "mL", "정", "캡슐", "회", "번", "일", "주", "개월"):
        assert unit in prompt

    # 2. Forbidden medical claim words
    for word in ("효능", "치료", "예방", "부작용", "상호작용"):
        assert word in prompt

    # 3. Forbidden prescription change terms
    for term in ("중단", "끊기/끊어", "증량", "감량", "늘리기", "줄이기", "횟수 변경", "용량 변경", "복용 변경"):
        assert term in prompt

    # 4. MFDS evidence replication prohibition and NULL fallback
    assert "text=null, evidence_slots=[]" in prompt
    assert "복제하거나 변형하여 지시하지 마십시오" in prompt

    # 5. Neutral symptom phrasing only, no therapeutic claim extension
    assert "...이 나타날 수 있습니다" in prompt
    for claim in ("낫다", "완화", "개선", "유발", "조절", "관리"):
        assert claim in prompt

    # 6. Fixed general_notice sentence
    assert f'general_notice는 정확히 다음 한 문장만 사용: "{CLOSED_DEMO_GENERAL_NOTICE}"' in prompt

    # 7. Safe translation only, otherwise null
    assert "변환 불가능하면 null 처리하십시오" in prompt


def test_closed_demo_general_notice_safety_and_alignment() -> None:
    """Verifies that CLOSED_DEMO_GENERAL_NOTICE passes safety rules while old notice fails closed."""
    # 1. New approved notice passes _validate_text cleanly
    _validate_text(CLOSED_DEMO_GENERAL_NOTICE)

    # 2. Old notice fails closed due to ambiguous word particle match in _validate_text
    old_notice = "처방된 복약 시간과 일정을 지켜 복용해 주세요."
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        _validate_text(old_notice)
    assert exc_info.value.rule_id == RULE_NUMERIC_IN_AI_TEXT

    # 3. Prompt contains CLOSED_DEMO_GENERAL_NOTICE
    assert CLOSED_DEMO_GENERAL_NOTICE in GUIDE_CLOSED_DEMO_SYSTEM_INSTRUCTIONS


def test_validate_closed_demo_draft_rejects_unapproved_general_notice() -> None:
    """Draft with general_notice different from CLOSED_DEMO_GENERAL_NOTICE must fail closed."""
    evidences = {0: (_make_evidence(1),)}
    draft = ClosedDemoGuideDraft(
        medications=[
            ClosedDemoMedicationGuidance(
                source_index=0,
                medication_caution=ClosedDemoGuidanceField(text="정해진 시간에 복용하세요.", evidence_slots=[1]),
                food_and_drink=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                alcohol_and_smoking=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                possible_discomfort=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                seek_medical_care=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
                pregnancy_and_breastfeeding=ClosedDemoGuidanceField(text=None, evidence_slots=[]),
            )
        ],
        general_notice="임의로 작성된 다른 공통 안내 문장입니다.",
    )
    with pytest.raises(GuideGenerationSafetyError) as exc_info:
        validate_closed_demo_draft(draft, expected_count=1, evidences_by_index=evidences)
    assert exc_info.value.rule_id == RULE_UNAPPROVED_GENERAL_NOTICE
