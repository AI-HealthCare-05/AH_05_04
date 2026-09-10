from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from ai_worker.tasks.rag.evidence_gate import (
    EvidenceGateExecutionStatus,
    EvidenceGateOutcome,
    EvidenceGateReason,
    EvidenceGateTrace,
    EvidenceStatus,
    GatePassedKnowledgeEvidenceSelection,
    canonical_gate_selection_hash,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    CanonicalScore,
    EvidenceSearchStage,
    ImmutableArtifactRef,
    KnowledgeEvidenceCandidate,
    KnowledgeEvidenceProvenance,
    SensitiveText,
    StageSignal,
    UntrustedKnowledgeEvidenceSelection,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineEvidenceBinding,
    ApprovedGuidelineFallback,
    GuidelineActionClass,
    GuidelineApprovalVerificationSuccess,
    GuidelineCardDraft,
    GuidelineCardReason,
    GuidelineCardRequest,
    GuidelineCardStatus,
    GuidelineCitationDraft,
    GuidelineClaimDraft,
    GuidelineFallbackCode,
    GuidelineGenerationFailure,
    GuidelineGenerationProvenance,
    GuidelineScope,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
)
from ai_worker.tasks.rag.guideline_card import (
    finalize_guideline_card as _finalize_guideline_card,
)
from ai_worker.tests.rag import test_evidence_gate as rag14_fixture

EVALUATED_AT = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
FOOD_AVOIDANCE_TEXT = (
    "이 약을 복용하는 동안 과도한 음주는 피하고 임의로 복용을 중단하지 마세요. 궁금한 점은 약사와 상담하세요."
)


def artifact(code: str, digest: str = "a" * 64) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(code, f"{code}@synthetic-1", digest)


class SyntheticApprovalVerifier:
    def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
        return GuidelineApprovalVerificationSuccess(
            artifact_ref=artifact_ref,
            verifier_artifact_ref=artifact("guideline-approval-verifier"),
        )


def finalize_guideline_card(request: GuidelineCardRequest):
    return _finalize_guideline_card(request, approval_verifier=SyntheticApprovalVerifier())


def gate_passed_selection(
    *,
    evidence_key: str = "knowledge:guideline-1",
    locator: str = "$.items[0].useMethodQesitm",
    source_version: str = "api:" + "1" * 64,
) -> GatePassedKnowledgeEvidenceSelection:
    text = FOOD_AVOIDANCE_TEXT
    provenance = KnowledgeEvidenceProvenance(
        evidence_key=evidence_key,
        knowledge_chunk_ref="chunk-guideline-1",
        evidence_index_ref=artifact("knowledge-index"),
        source_snapshot_ref=artifact("source-snapshot"),
        source_version=source_version,
        locator=locator,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        canonicalization_spec_version="knowledge-text@1",
    )
    selection = UntrustedKnowledgeEvidenceSelection(
        candidate=KnowledgeEvidenceCandidate(
            provenance=provenance,
            content_text=SensitiveText(text),
            stage_signals=(StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),),
        ),
        rerank_rank=1,
        rerank_score=CanonicalScore("0.9"),
    )
    return GatePassedKnowledgeEvidenceSelection(
        selection=selection,
        assessment_artifact_ref=artifact("assessment"),
        eligibility_receipt_ref=artifact("eligibility-receipt"),
        retrieval_receipt_ref=artifact("retrieval-receipt"),
        verifier_artifact_ref=artifact("eligibility-verifier"),
    )


def successful_evidence_gate(
    *selections: GatePassedKnowledgeEvidenceSelection,
) -> EvidenceGateOutcome:
    passed = selections or (gate_passed_selection(),)
    return EvidenceGateOutcome(
        execution_status=EvidenceGateExecutionStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        reason=EvidenceGateReason.EVIDENCE_SUFFICIENT,
        gate_passed_selections=passed,
        trace=EvidenceGateTrace(
            policy_ref=artifact("evidence-gate-policy"),
            retrieval_receipt_ref=passed[0].retrieval_receipt_ref,
            evaluated_at=EVALUATED_AT,
            assessment_artifact_refs=tuple(item.assessment_artifact_ref for item in passed),
            selected_evidence_keys=tuple(item.selection.candidate.provenance.evidence_key for item in passed),
        ),
    )


def medication() -> MedicationIdentityRef:
    return MedicationIdentityRef(
        prescription_version_medication_id="11111111-1111-4111-8111-111111111111",
        code_system="MFDS_ITEM_SEQ",
        canonical_code="SYNTHETIC-ITEM-001",
    )


def approved_fallbacks() -> tuple[ApprovedGuidelineFallback, ...]:
    return tuple(
        ApprovedGuidelineFallback.create(
            "guideline-fallback",
            f"guideline-fallback@synthetic-{code.value.lower()}",
            code=code,
            text=SensitiveText("현재는 승인된 안내를 제공할 수 없습니다. 의사 또는 약사와 상담하세요."),
        )
        for code in GuidelineFallbackCode
    )


def valid_request() -> GuidelineCardRequest:
    selected = gate_passed_selection()
    evidence = selected.selection.candidate.provenance
    identity = medication()
    action_text = FOOD_AVOIDANCE_TEXT
    uncertainty_text = "승인된 근거 범위 밖의 내용은 확인할 수 없습니다."
    consultation_text = "불편하거나 궁금한 점은 의사 또는 약사와 상담하세요."
    binding = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@synthetic-1",
        medication_identity=identity,
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=GuidelineActionClass.FOOD_AVOIDANCE,
        evidence_key=evidence.evidence_key,
        assessment_artifact_ref=selected.assessment_artifact_ref,
        selection_projection_sha256=canonical_gate_selection_hash(selected.selection),
        action_text_sha256=hashlib.sha256(action_text.encode()).hexdigest(),
    )
    return GuidelineCardRequest(
        medication_identities=(identity,),
        evidence_gate_outcome=successful_evidence_gate(selected),
        draft=GuidelineCardDraft(
            claims=(
                GuidelineClaimDraft(
                    claim_key="claim-food-1",
                    medication_identity=identity,
                    scope=GuidelineScope.FOOD_CAUTION,
                    action_class=GuidelineActionClass.FOOD_AVOIDANCE,
                    action_text=SensitiveText(action_text),
                    citations=(
                        GuidelineCitationDraft(
                            evidence_key=evidence.evidence_key,
                            source_snapshot_ref=evidence.source_snapshot_ref,
                            source_version=evidence.source_version,
                            locator=evidence.locator,
                            content_sha256=evidence.content_sha256,
                        ),
                    ),
                ),
            ),
            uncertainty_text=SensitiveText(uncertainty_text),
            consultation_text=SensitiveText(consultation_text),
        ),
        generation_failure=None,
        policy=VersionedGuidelinePolicy.create(
            "guideline-policy",
            "guideline-policy@synthetic-1",
            maximum_claims=4,
            uncertainty_text_sha256=hashlib.sha256(uncertainty_text.encode()).hexdigest(),
            consultation_text_sha256=hashlib.sha256(consultation_text.encode()).hexdigest(),
        ),
        provenance=GuidelineGenerationProvenance(
            prompt_ref=artifact("guideline-prompt"),
            model_ref=artifact("guideline-model"),
            parser_ref=artifact("guideline-parser"),
            validator_ref=artifact("guideline-validator"),
        ),
        approved_fallbacks=approved_fallbacks(),
        evaluated_at=EVALUATED_AT,
        approved_evidence_bindings=(binding,),
    )


def request_with_approved_action(text: str) -> GuidelineCardRequest:
    request = valid_request()
    assert request.draft is not None
    claim = replace(request.draft.claims[0], action_text=SensitiveText(text))
    current = request.approved_evidence_bindings[0]
    binding = ApprovedGuidelineEvidenceBinding.create(
        current.artifact_ref.artifact_code,
        current.artifact_ref.version,
        medication_identity=current.medication_identity,
        scope=current.scope,
        action_class=current.action_class,
        evidence_key=current.evidence_key,
        assessment_artifact_ref=current.assessment_artifact_ref,
        selection_projection_sha256=current.selection_projection_sha256,
        action_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    return replace(
        request,
        draft=replace(request.draft, claims=(claim,)),
        approved_evidence_bindings=(binding,),
    )


def test_valid_card_binds_each_claim_to_medication_and_gate_passed_citation() -> None:
    request = valid_request()
    selected = request.evidence_gate_outcome.gate_passed_selections[0]
    outcome = finalize_guideline_card(request)

    assert outcome.status is GuidelineCardStatus.GENERATED
    assert outcome.reason is GuidelineCardReason.CARD_GENERATED
    assert outcome.fallback_code is None
    assert outcome.fallback is None
    assert outcome.card is not None
    assert outcome.card.claims[0].medication_identity == medication()
    assert outcome.card.claims[0].citations[0].assessment_artifact_ref == selected.assessment_artifact_ref
    assert outcome.card.claims[0].citations[0].locator == selected.selection.candidate.provenance.locator
    assert outcome.card.claims[0].citations[0].source_type.value == "LIFESTYLE_GUIDELINE"
    assert outcome.card.provenance.prompt_ref == request.provenance.prompt_ref
    assert outcome.card.provenance.model_ref == request.provenance.model_ref
    assert outcome.card.provenance.parser_ref == request.provenance.parser_ref
    assert outcome.card.provenance.validator_ref == request.provenance.validator_ref
    assert outcome.card.provenance.guideline_policy_ref == request.policy.artifact_ref


def test_card_accepts_an_actual_rag14_evidence_gate_success() -> None:
    request = valid_request()
    assert request.draft is not None
    action_text = request.draft.claims[0].action_text.reveal()
    selection = rag14_fixture.selection(text=action_text)
    receipt = rag14_fixture.retrieval_receipt(selections=(selection,))
    assessment = rag14_fixture.assessment(selection, gate_retrieval_receipt=receipt)
    gate = rag14_fixture.evaluate(
        rag14_fixture.request(
            (selection,),
            (assessment,),
            gate_retrieval_receipt=receipt,
        )
    )
    passed = gate.gate_passed_selections[0]
    evidence = passed.selection.candidate.provenance
    citation = GuidelineCitationDraft(
        evidence_key=evidence.evidence_key,
        source_snapshot_ref=evidence.source_snapshot_ref,
        source_version=evidence.source_version,
        locator=evidence.locator,
        content_sha256=evidence.content_sha256,
    )
    claim = replace(request.draft.claims[0], citations=(citation,))
    binding = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@synthetic-rag14",
        medication_identity=claim.medication_identity,
        scope=claim.scope,
        action_class=claim.action_class,
        evidence_key=evidence.evidence_key,
        assessment_artifact_ref=passed.assessment_artifact_ref,
        selection_projection_sha256=canonical_gate_selection_hash(passed.selection),
        action_text_sha256=hashlib.sha256(action_text.encode()).hexdigest(),
    )

    outcome = finalize_guideline_card(
        replace(
            request,
            evidence_gate_outcome=gate,
            draft=replace(request.draft, claims=(claim,)),
            evaluated_at=rag14_fixture.NOW,
            approved_evidence_bindings=(binding,),
        )
    )

    assert outcome.status is GuidelineCardStatus.GENERATED


def test_no_approved_evidence_discards_draft_and_returns_approved_fallback() -> None:
    request = valid_request()
    gate = EvidenceGateOutcome(
        EvidenceGateExecutionStatus.NO_RESULT,
        EvidenceStatus.INSUFFICIENT,
        EvidenceGateReason.EVIDENCE_INSUFFICIENT,
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=gate))

    assert outcome.status is GuidelineCardStatus.NO_RESULT
    assert outcome.reason is GuidelineCardReason.EVIDENCE_INSUFFICIENT
    assert outcome.fallback_code is GuidelineFallbackCode.NO_APPROVED_EVIDENCE
    assert outcome.card is None
    assert outcome.fallback is not None
    assert outcome.fallback.code is GuidelineFallbackCode.NO_APPROVED_EVIDENCE


def test_conflicting_evidence_returns_conflict_fallback() -> None:
    request = valid_request()
    gate = EvidenceGateOutcome(
        EvidenceGateExecutionStatus.NO_RESULT,
        EvidenceStatus.CONFLICTED,
        EvidenceGateReason.EVIDENCE_CONFLICTED,
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=gate))

    assert outcome.status is GuidelineCardStatus.NO_RESULT
    assert outcome.reason is GuidelineCardReason.EVIDENCE_CONFLICTED
    assert outcome.fallback_code is GuidelineFallbackCode.CONFLICTING_EVIDENCE
    assert outcome.card is None


def test_stale_source_is_not_execution_context_stale() -> None:
    request = valid_request()
    gate = EvidenceGateOutcome(
        EvidenceGateExecutionStatus.NO_RESULT,
        EvidenceStatus.STALE,
        EvidenceGateReason.EVIDENCE_STALE,
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=gate))

    assert outcome.status is GuidelineCardStatus.NO_RESULT
    assert outcome.reason is GuidelineCardReason.EVIDENCE_STALE
    assert outcome.fallback_code is GuidelineFallbackCode.NO_APPROVED_EVIDENCE


def test_evidence_gate_validation_error_discards_draft() -> None:
    request = valid_request()
    gate = EvidenceGateOutcome(
        EvidenceGateExecutionStatus.VALIDATION_ERROR,
        None,
        EvidenceGateReason.REQUEST_INVALID,
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=gate))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason is GuidelineCardReason.VALIDATION_FAILED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
    assert outcome.card is None


def test_evidence_gate_dependency_error_uses_dependency_fallback() -> None:
    request = valid_request()
    gate = EvidenceGateOutcome(
        EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
        None,
        EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR,
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=gate))

    assert outcome.status is GuidelineCardStatus.NO_RESULT
    assert outcome.reason is GuidelineCardReason.DEPENDENCY_UNAVAILABLE
    assert outcome.fallback_code is GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE


def test_generation_failures_have_deterministic_status_and_fallback() -> None:
    request = valid_request()
    expected = {
        GuidelineGenerationFailure.PROVIDER_TIMEOUT: (
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.PROVIDER_TIMEOUT,
            GuidelineFallbackCode.PROVIDER_TIMEOUT,
        ),
        GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE: (
            GuidelineCardStatus.NO_RESULT,
            GuidelineCardReason.DEPENDENCY_UNAVAILABLE,
            GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE,
        ),
        GuidelineGenerationFailure.VALIDATION_FAILED: (
            GuidelineCardStatus.VALIDATION_REJECTED,
            GuidelineCardReason.VALIDATION_FAILED,
            GuidelineFallbackCode.VALIDATION_FAILED,
        ),
        GuidelineGenerationFailure.PRESCRIPTION_STALE: (
            GuidelineCardStatus.STALE,
            GuidelineCardReason.PRESCRIPTION_STALE,
            GuidelineFallbackCode.PRESCRIPTION_STALE,
        ),
        GuidelineGenerationFailure.EXECUTION_CONTEXT_STALE: (
            GuidelineCardStatus.STALE,
            GuidelineCardReason.EXECUTION_CONTEXT_STALE,
            GuidelineFallbackCode.EXECUTION_CONTEXT_STALE,
        ),
        GuidelineGenerationFailure.UNSUPPORTED_REQUEST: (
            GuidelineCardStatus.LIMITED,
            GuidelineCardReason.UNSUPPORTED_REQUEST,
            GuidelineFallbackCode.UNSUPPORTED_REQUEST,
        ),
    }

    for failure, wanted in expected.items():
        outcome = finalize_guideline_card(replace(request, draft=None, generation_failure=failure))
        assert (outcome.status, outcome.reason, outcome.fallback_code) == wanted
        assert outcome.card is None
        assert outcome.fallback is not None


def test_generation_failure_discards_partial_draft_without_changing_failure_code() -> None:
    request = valid_request()

    outcome = finalize_guideline_card(replace(request, generation_failure=GuidelineGenerationFailure.PROVIDER_TIMEOUT))

    assert outcome.status is GuidelineCardStatus.NO_RESULT
    assert outcome.reason is GuidelineCardReason.PROVIDER_TIMEOUT
    assert outcome.fallback_code is GuidelineFallbackCode.PROVIDER_TIMEOUT
    assert outcome.card is None


def test_citation_locator_or_hash_mismatch_discards_entire_card() -> None:
    request = valid_request()
    assert request.draft is not None
    claim = request.draft.claims[0]
    bad_citation = replace(claim.citations[0], locator="$.items[1].useMethodQesitm")
    bad_draft = replace(request.draft, claims=(replace(claim, citations=(bad_citation,)),))

    outcome = finalize_guideline_card(replace(request, draft=bad_draft))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
    assert outcome.card is None


def test_claim_without_citation_is_rejected() -> None:
    request = valid_request()
    assert request.draft is not None
    claim = replace(request.draft.claims[0], citations=())

    outcome = finalize_guideline_card(replace(request, draft=replace(request.draft, claims=(claim,))))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_claim_for_unpinned_medication_is_rejected() -> None:
    request = valid_request()
    assert request.draft is not None
    other = replace(medication(), prescription_version_medication_id="22222222-2222-4222-8222-222222222222")
    claim = replace(request.draft.claims[0], medication_identity=other)

    outcome = finalize_guideline_card(replace(request, draft=replace(request.draft, claims=(claim,))))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_forbidden_prescription_change_or_diagnosis_discards_card() -> None:
    request = valid_request()
    assert request.draft is not None
    forbidden_texts = (
        "오늘부터 이 약의 복용을 중단하세요.",
        "용량을 늘리세요.",
        "복용 시간을 변경하세요.",
        "이 증상은 고혈압으로 진단됩니다.",
        "이 약은 하루에 세 알을 드세요.",
        "이 증상은 고혈압입니다.",
        "식사 후 이 약을 두 배로 드세요.",
        "식사 후 이 약을 반 알씩 드세요.",
        "식사 전에 이 약을 드세요.",
        "음주할 때 다른 약으로 교체하세요.",
        "음식 주의와 함께 고혈압이 확실합니다.",
        "음식 주의 사항으로 고혈압으로 보여요.",
    )

    for text in forbidden_texts:
        outcome = finalize_guideline_card(request_with_approved_action(text))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_safe_negative_prescription_change_warning_is_allowed() -> None:
    outcome = finalize_guideline_card(request_with_approved_action(FOOD_AVOIDANCE_TEXT))

    assert outcome.status is GuidelineCardStatus.GENERATED


def test_foreign_healthcare_system_or_non_korean_copy_is_rejected() -> None:
    request = valid_request()
    assert request.draft is not None
    forbidden_texts = (
        "Call 911 or contact the FDA.",
        "미국 의료기관에 전화하세요.",
        "일본 병원으로 전화하세요.",
        "음주 관련 문의는 Mayo Clinic에 연락하세요.",
        "음주 관련 문의는 메이요 클리닉에 연락하세요.",
        "음주 안내: Take two pills daily and call your doctor.",
        "자세한 내용은 https://example.com에서 확인하세요.",
    )

    for text in forbidden_texts:
        outcome = finalize_guideline_card(request_with_approved_action(text))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_scope_keyword_collision_does_not_authorize_unrelated_medical_action() -> None:
    outcome = finalize_guideline_card(request_with_approved_action("수술을 받으세요."))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.card is None


def test_tampered_approved_fallback_is_not_exposed() -> None:
    request = valid_request()
    fallback = next(item for item in request.approved_fallbacks if item.code is GuidelineFallbackCode.PROVIDER_TIMEOUT)
    tampered = replace(fallback, text=SensitiveText("변조된 문구"))
    fallbacks = tuple(
        tampered if item.code is GuidelineFallbackCode.PROVIDER_TIMEOUT else item for item in request.approved_fallbacks
    )

    outcome = finalize_guideline_card(
        replace(
            request,
            draft=None,
            generation_failure=GuidelineGenerationFailure.PROVIDER_TIMEOUT,
            approved_fallbacks=fallbacks,
        )
    )

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason is GuidelineCardReason.VALIDATION_FAILED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
    assert outcome.fallback is None


def test_self_hashed_unsafe_fallback_is_rejected_before_exposure() -> None:
    request = valid_request()
    unsafe = ApprovedGuidelineFallback.create(
        "guideline-fallback",
        "guideline-fallback@synthetic-provider-timeout",
        code=GuidelineFallbackCode.PROVIDER_TIMEOUT,
        text=SensitiveText("이 증상은 고혈압입니다. 이 약의 복용을 중단하세요."),
    )
    fallbacks = tuple(
        unsafe if item.code is GuidelineFallbackCode.PROVIDER_TIMEOUT else item for item in request.approved_fallbacks
    )

    outcome = finalize_guideline_card(
        replace(
            request,
            draft=None,
            generation_failure=GuidelineGenerationFailure.PROVIDER_TIMEOUT,
            approved_fallbacks=fallbacks,
        )
    )

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback is None


def test_finalized_fallback_does_not_alias_mutable_sensitive_input() -> None:
    request = valid_request()
    source = next(item for item in request.approved_fallbacks if item.code is GuidelineFallbackCode.PROVIDER_TIMEOUT)
    original = source.text.reveal()

    outcome = finalize_guideline_card(
        replace(request, draft=None, generation_failure=GuidelineGenerationFailure.PROVIDER_TIMEOUT)
    )
    assert outcome.fallback is not None
    object.__setattr__(source.text, "_SensitiveText__value", "사후 변조된 문구")

    assert outcome.fallback.text.reveal() == original


def test_unknown_or_duplicate_citation_is_rejected() -> None:
    request = valid_request()
    assert request.draft is not None
    claim = request.draft.claims[0]
    unknown = replace(claim.citations[0], evidence_key="knowledge:unknown")

    for citations in ((unknown,), (claim.citations[0], claim.citations[0])):
        outcome = finalize_guideline_card(
            replace(request, draft=replace(request.draft, claims=(replace(claim, citations=citations),)))
        )
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_duplicate_claim_key_is_rejected() -> None:
    request = valid_request()
    assert request.draft is not None
    claim = request.draft.claims[0]

    outcome = finalize_guideline_card(replace(request, draft=replace(request.draft, claims=(claim, claim))))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_unsafe_uncertainty_or_consultation_copy_is_rejected() -> None:
    request = valid_request()
    assert request.draft is not None
    unsafe_drafts = (
        replace(request.draft, uncertainty_text=SensitiveText("See https://example.com")),
        replace(request.draft, consultation_text=SensitiveText("Call 911")),
        replace(request.draft, consultation_text=SensitiveText("필요하면 주변 사람에게 물어보세요.")),
    )

    for draft in unsafe_drafts:
        outcome = finalize_guideline_card(replace(request, draft=draft))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_tampered_policy_or_generation_provenance_is_rejected() -> None:
    request = valid_request()
    bad_policy = replace(request.policy, maximum_claims=5)
    bad_provenance = replace(
        request.provenance, parser_ref=replace(request.provenance.parser_ref, content_sha256="bad")
    )

    for changed in (
        replace(request, policy=bad_policy),
        replace(request, provenance=bad_provenance),
    ):
        outcome = finalize_guideline_card(changed)
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_inconsistent_evidence_gate_outcome_is_rejected() -> None:
    request = valid_request()
    inconsistent = EvidenceGateOutcome(
        EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
        None,
        EvidenceGateReason.REQUEST_INVALID,
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=inconsistent))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason is GuidelineCardReason.VALIDATION_FAILED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_mutated_gate_passed_content_is_rejected() -> None:
    request = valid_request()
    selected = request.evidence_gate_outcome.gate_passed_selections[0]
    candidate = selected.selection.candidate
    changed_candidate = replace(candidate, content_text=SensitiveText("변조된 합성 근거"))
    changed_selection = replace(selected.selection, candidate=changed_candidate)
    changed_gate = replace(
        request.evidence_gate_outcome,
        gate_passed_selections=(replace(selected, selection=changed_selection),),
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=changed_gate))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_replaced_gate_provenance_cannot_reuse_original_assessment() -> None:
    request = valid_request()
    assert request.draft is not None
    selected = request.evidence_gate_outcome.gate_passed_selections[0]
    provenance = replace(
        selected.selection.candidate.provenance,
        source_snapshot_ref=artifact("attacker-source", "b" * 64),
        source_version="api:" + "2" * 64,
        locator="$.attacker.injected",
    )
    candidate = replace(selected.selection.candidate, provenance=provenance)
    changed = replace(selected, selection=replace(selected.selection, candidate=candidate))
    changed_gate = replace(request.evidence_gate_outcome, gate_passed_selections=(changed,))
    citation = replace(
        request.draft.claims[0].citations[0],
        source_snapshot_ref=provenance.source_snapshot_ref,
        source_version=provenance.source_version,
        locator=provenance.locator,
    )
    changed_draft = replace(
        request.draft,
        claims=(replace(request.draft.claims[0], citations=(citation,)),),
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=changed_gate, draft=changed_draft))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.card is None


def test_missing_or_replayed_evidence_gate_trace_is_rejected() -> None:
    request = valid_request()
    without_trace = replace(request.evidence_gate_outcome, trace=None)
    replayed = replace(request, evaluated_at=datetime(2036, 9, 10, 3, 0, tzinfo=UTC))

    for changed in (replace(request, evidence_gate_outcome=without_trace), replayed):
        outcome = finalize_guideline_card(changed)
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_malformed_gate_selection_is_rejected() -> None:
    request = valid_request()
    selected = request.evidence_gate_outcome.gate_passed_selections[0]
    malformed_candidate = replace(selected.selection.candidate, stage_signals=())
    malformed_selection = replace(
        selected.selection,
        candidate=malformed_candidate,
        rerank_score=CanonicalScore("not-a-score"),
    )
    malformed_gate = replace(
        request.evidence_gate_outcome,
        gate_passed_selections=(replace(selected, selection=malformed_selection),),
    )

    outcome = finalize_guideline_card(replace(request, evidence_gate_outcome=malformed_gate))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_claim_scope_must_match_approved_medication_evidence_binding() -> None:
    request = valid_request()
    selected = request.evidence_gate_outcome.gate_passed_selections[0]
    evidence_key = selected.selection.candidate.provenance.evidence_key
    matching = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@synthetic-1",
        medication_identity=medication(),
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=GuidelineActionClass.FOOD_AVOIDANCE,
        evidence_key=evidence_key,
        assessment_artifact_ref=selected.assessment_artifact_ref,
        selection_projection_sha256=canonical_gate_selection_hash(selected.selection),
        action_text_sha256=hashlib.sha256(
            (request.draft.claims[0].action_text.reveal() if request.draft is not None else "").encode()
        ).hexdigest(),
    )
    mismatching = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@synthetic-2",
        medication_identity=medication(),
        scope=GuidelineScope.DAILY_ACTIVITY,
        action_class=GuidelineActionClass.DAILY_ACTIVITY_PRECAUTION,
        evidence_key=evidence_key,
        assessment_artifact_ref=selected.assessment_artifact_ref,
        selection_projection_sha256=canonical_gate_selection_hash(selected.selection),
        action_text_sha256=hashlib.sha256(
            (request.draft.claims[0].action_text.reveal() if request.draft is not None else "").encode()
        ).hexdigest(),
    )

    accepted = finalize_guideline_card(replace(request, approved_evidence_bindings=(matching,)))
    rejected = finalize_guideline_card(replace(request, approved_evidence_bindings=(mismatching,)))

    assert accepted.status is GuidelineCardStatus.GENERATED
    assert rejected.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert rejected.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_claim_copy_must_match_approved_evidence_binding() -> None:
    request = valid_request()
    assert request.draft is not None
    changed_claim = replace(
        request.draft.claims[0],
        action_text=SensitiveText("매일 가벼운 산책을 하세요."),
    )

    outcome = finalize_guideline_card(replace(request, draft=replace(request.draft, claims=(changed_claim,))))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_notice_copy_must_match_versioned_policy() -> None:
    request = valid_request()
    assert request.draft is not None
    changed_draft = replace(
        request.draft,
        uncertainty_text=SensitiveText("이 안내는 모든 사람에게 안전합니다."),
    )

    outcome = finalize_guideline_card(replace(request, draft=changed_draft))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_finalized_card_does_not_alias_mutable_sensitive_input() -> None:
    request = valid_request()
    assert request.draft is not None
    original = request.draft.claims[0].action_text.reveal()

    outcome = finalize_guideline_card(request)
    assert outcome.card is not None
    artifact_ref = outcome.card.artifact_ref
    object.__setattr__(
        request.draft.claims[0].action_text,
        "_SensitiveText__value",
        "사후 변조된 문구",
    )

    assert outcome.card.claims[0].action_text.reveal() == original
    assert outcome.card.artifact_ref == artifact_ref


def test_finalized_card_snapshots_nested_identity_and_artifact_refs() -> None:
    request = valid_request()
    outcome = finalize_guideline_card(request)
    assert outcome.card is not None
    original_identity = outcome.card.claims[0].medication_identity.canonical_code
    original_prompt = outcome.card.provenance.prompt_ref.artifact_code
    original_snapshot = outcome.card.claims[0].citations[0].source_snapshot_ref.artifact_code
    artifact_ref = outcome.card.artifact_ref

    object.__setattr__(request.medication_identities[0], "canonical_code", "TAMPERED")
    object.__setattr__(request.provenance.prompt_ref, "artifact_code", "tampered-prompt")
    selected = request.evidence_gate_outcome.gate_passed_selections[0]
    object.__setattr__(
        selected.selection.candidate.provenance.source_snapshot_ref,
        "artifact_code",
        "tampered-source",
    )

    assert outcome.card.claims[0].medication_identity.canonical_code == original_identity
    assert outcome.card.provenance.prompt_ref.artifact_code == original_prompt
    assert outcome.card.claims[0].citations[0].source_snapshot_ref.artifact_code == original_snapshot
    assert outcome.card.artifact_ref == artifact_ref


def test_non_exact_string_boundary_fails_closed_without_exception() -> None:
    class ExplodingString(str):
        def __deepcopy__(self, memo: object) -> str:
            raise RuntimeError("boom")

    request = valid_request()
    first = request.approved_fallbacks[0]
    malformed = replace(
        first,
        artifact_ref=replace(first.artifact_ref, artifact_code=ExplodingString(first.artifact_ref.artifact_code)),
    )
    fallbacks = (malformed, *request.approved_fallbacks[1:])

    outcome = finalize_guideline_card(replace(request, approved_fallbacks=fallbacks))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback is None


def test_self_hashed_artifacts_require_an_authoritative_approval_verifier() -> None:
    request = valid_request()

    class RejectingVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> object:
            return object()

    outcome = _finalize_guideline_card(request, approval_verifier=RejectingVerifier())  # type: ignore[arg-type]

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
    assert outcome.fallback is None


def test_malformed_approved_binding_fails_closed_without_attribute_error() -> None:
    request = valid_request()

    outcome = finalize_guideline_card(replace(request, approved_evidence_bindings=(object(),)))  # type: ignore[arg-type]

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_malformed_medication_identity_and_datetime_subclass_fail_closed() -> None:
    request = valid_request()
    malformed_identity = MedicationIdentityRef(123, "MFDS_ITEM_SEQ", "SYNTHETIC-ITEM-001")  # type: ignore[arg-type]

    class DatetimeSubclass(datetime):
        pass

    malformed_time = DatetimeSubclass(
        2026,
        9,
        10,
        3,
        0,
        tzinfo=UTC,
    )

    for changed in (
        replace(request, medication_identities=(malformed_identity,)),
        replace(request, evaluated_at=malformed_time),
    ):
        outcome = finalize_guideline_card(changed)
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED


def test_malformed_claim_medication_identity_fails_closed_before_hashing() -> None:
    request = valid_request()
    assert request.draft is not None
    malformed_identity = MedicationIdentityRef(
        "11111111-1111-4111-8111-111111111111",
        [],  # type: ignore[arg-type]
        "SYNTHETIC-ITEM-001",
    )
    claim = replace(request.draft.claims[0], medication_identity=malformed_identity)

    outcome = finalize_guideline_card(replace(request, draft=replace(request.draft, claims=(claim,))))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
