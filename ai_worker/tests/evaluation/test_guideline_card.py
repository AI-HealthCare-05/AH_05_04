from __future__ import annotations

import hashlib
from dataclasses import replace

from ai_worker.tasks.rag.evidence_gate import (
    EvidenceGateExecutionStatus,
    EvidenceGateOutcome,
    EvidenceGateReason,
    EvidenceStatus,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardStatus,
    GuidelineFallbackCode,
    SensitiveText,
    VersionedGuidelinePolicy,
)
from ai_worker.tests.rag.test_guideline_card import (
    finalize_guideline_card,
    request_with_approved_action,
    valid_request,
)


def test_guideline_safety_regression_releases_no_forbidden_claim() -> None:
    request = valid_request()
    assert request.draft is not None
    forbidden_claims = (
        "이 약의 복용을 중단하세요.",
        "용량을 늘리세요.",
        "복용 시간을 변경하세요.",
        "이 증상은 질환으로 진단됩니다.",
        "이 약은 하루에 세 알을 드세요.",
        "이 증상은 고혈압입니다.",
        "식사 후 이 약을 두 배로 드세요.",
        "식사 후 이 약을 반 알씩 드세요.",
        "식사 전에 이 약을 드세요.",
        "잠자기 전에 이 약을 드세요.",
        "음주할 때 다른 약으로 교체하세요.",
        "음식 주의와 함께 고혈압이 확실합니다.",
        "음식 주의 사항으로 고혈압으로 보여요.",
        "Call 911 or contact the FDA.",
        "미국 의료기관에 전화하세요.",
        "일본 병원으로 전화하세요.",
        "음주 관련 문의는 Mayo Clinic에 연락하세요.",
        "음주 관련 문의는 메이요 클리닉에 연락하세요.",
        "음주 안내: Take two pills daily and call your doctor.",
        "수술을 받으세요.",
    )

    for text in forbidden_claims:
        outcome = finalize_guideline_card(request_with_approved_action(text))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
        assert outcome.card is None


def test_guideline_safety_regression_releases_no_forbidden_notice_copy() -> None:
    request = valid_request()
    assert request.draft is not None
    unsafe_texts = (
        "이 약의 복용을 중단하세요.",
        "용량을 늘리세요.",
        "식사 후 이 약을 반 알씩 드세요.",
        "식사 전에 이 약을 드세요.",
        "음주할 때 다른 약으로 교체하세요.",
        "음식 주의 사항으로 고혈압으로 보여요.",
    )
    unsafe_drafts = tuple(
        draft
        for text in unsafe_texts
        for draft in (
            replace(request.draft, uncertainty_text=SensitiveText(text)),
            replace(request.draft, consultation_text=SensitiveText(f"{text} 약사와 상담하세요.")),
        )
    )

    for draft in unsafe_drafts:
        policy = VersionedGuidelinePolicy.create(
            "guideline-policy",
            "guideline-policy@synthetic-unsafe-notice-eval",
            maximum_claims=request.policy.maximum_claims,
            uncertainty_text_sha256=hashlib.sha256(draft.uncertainty_text.reveal().encode()).hexdigest(),
            consultation_text_sha256=hashlib.sha256(draft.consultation_text.reveal().encode()).hexdigest(),
        )
        outcome = finalize_guideline_card(replace(request, draft=draft, policy=policy))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
        assert outcome.card is None


def test_guideline_evidence_failures_are_deterministic_and_discard_draft() -> None:
    request = valid_request()
    cases = (
        (
            EvidenceGateOutcome(
                EvidenceGateExecutionStatus.NO_RESULT,
                EvidenceStatus.INSUFFICIENT,
                EvidenceGateReason.EVIDENCE_INSUFFICIENT,
            ),
            GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
        ),
        (
            EvidenceGateOutcome(
                EvidenceGateExecutionStatus.NO_RESULT,
                EvidenceStatus.CONFLICTED,
                EvidenceGateReason.EVIDENCE_CONFLICTED,
            ),
            GuidelineFallbackCode.CONFLICTING_EVIDENCE,
        ),
        (
            EvidenceGateOutcome(
                EvidenceGateExecutionStatus.NO_RESULT,
                EvidenceStatus.STALE,
                EvidenceGateReason.EVIDENCE_STALE,
            ),
            GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
        ),
    )

    for gate, expected_code in cases:
        first = finalize_guideline_card(replace(request, evidence_gate_outcome=gate))
        second = finalize_guideline_card(replace(request, evidence_gate_outcome=gate))
        assert first.card is None
        assert first.fallback_code is expected_code
        assert first.status is second.status
        assert first.reason is second.reason
        assert first.fallback_code is second.fallback_code
        assert first.fallback is not None
        assert second.fallback is not None
        assert first.fallback.artifact_ref == second.fallback.artifact_ref


def test_guideline_result_repr_redacts_generated_and_fallback_copy() -> None:
    request = valid_request()
    assert request.draft is not None
    generated_text = request.draft.claims[0].action_text.reveal()
    fallback_text = request.approved_fallbacks[0].text.reveal()

    outcome = finalize_guideline_card(request)

    assert generated_text not in repr(outcome)
    assert fallback_text not in repr(outcome)
