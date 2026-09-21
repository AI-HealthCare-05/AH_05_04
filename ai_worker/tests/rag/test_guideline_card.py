# mypy: disable-error-code="arg-type, type-var, union-attr"

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    SensitiveText,
)
from ai_worker.tasks.rag.guide_aggregate_evidence import GuideAggregateEvidence, GuideAggregateEvidenceRun
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineEvidenceBinding,
    ApprovedGuidelineFallback,
    GuidelineActionClass,
    GuidelineApprovalVerificationFailure,
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
from ai_worker.tasks.rag.guideline_production_evidence import (
    ProductionGuidelineEvidence,
    ProductionGuidelineEvidenceSet,
    compute_production_guideline_evidence_selection_hash,
    project_guideline_evidence_from_handoff,
)
from ai_worker.tests.rag import test_guideline_production_evidence as handoff_fixture

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


def production_selection(
    *,
    evidence_key: str = "knowledge:guideline-1",
    locator: str = "$.items[0].useMethodQesitm",
    source_version: str = "api:" + "1" * 64,
    text: str = FOOD_AVOIDANCE_TEXT,
) -> ProductionGuidelineEvidence:
    """A production evidence selection as #774 projects it out of the #760 handoff."""
    return ProductionGuidelineEvidence(
        evidence_key=evidence_key,
        source_snapshot_id=handoff_fixture.uuid_of("3"),
        source_snapshot_member_id=handoff_fixture.uuid_of("4"),
        source_code="MFDS_DUR",
        source_version=source_version,
        locator=locator,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        content_text=SensitiveText(text),
        retrieval_receipt_ref=artifact("retrieval-receipt"),
        eligibility_receipt_ref=artifact("eligibility-receipt"),
        assessment_artifact_ref=artifact("assessment"),
        verifier_artifact_ref=artifact("assessment-verifier"),
    )


def production_evidence_set(
    *selections: ProductionGuidelineEvidence,
) -> ProductionGuidelineEvidenceSet:
    return ProductionGuidelineEvidenceSet(
        evaluated_at=EVALUATED_AT,
        handoff_sha256="e" * 64,
        selections=selections or (production_selection(),),
    )


def citation_draft_for(evidence: ProductionGuidelineEvidence) -> GuidelineCitationDraft:
    return GuidelineCitationDraft(
        evidence_key=evidence.evidence_key,
        source_snapshot_id=evidence.source_snapshot_id,
        source_snapshot_member_id=evidence.source_snapshot_member_id,
        source_code=evidence.source_code,
        source_version=evidence.source_version,
        locator=evidence.locator,
        content_sha256=evidence.content_sha256,
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
    evidence = production_selection()
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
        assessment_artifact_ref=evidence.assessment_artifact_ref,
        selection_projection_sha256=compute_production_guideline_evidence_selection_hash(evidence),
        action_text_sha256=hashlib.sha256(action_text.encode()).hexdigest(),
    )
    return GuidelineCardRequest(
        medication_identities=(identity,),
        evidence=production_evidence_set(evidence),
        draft=GuidelineCardDraft(
            claims=(
                GuidelineClaimDraft(
                    claim_key="claim-food-1",
                    medication_identity=identity,
                    scope=GuidelineScope.FOOD_CAUTION,
                    action_class=GuidelineActionClass.FOOD_AVOIDANCE,
                    action_text=SensitiveText(action_text),
                    citations=(citation_draft_for(evidence),),
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


def test_aggregate_evidence_binds_two_medications_with_the_same_evidence_key_by_snapshot_and_receipt() -> None:
    first_request = valid_request()
    first = production_selection()
    second_identity = MedicationIdentityRef(
        prescription_version_medication_id="22222222-2222-4222-8222-222222222222",
        code_system="MFDS_ITEM_SEQ",
        canonical_code="SYNTHETIC-ITEM-002",
    )
    second = replace(
        first,
        source_snapshot_id=handoff_fixture.uuid_of("8"),
        source_snapshot_member_id=handoff_fixture.uuid_of("9"),
        retrieval_receipt_ref=artifact("retrieval-receipt-second", "b" * 64),
    )
    second_binding = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@synthetic-2",
        medication_identity=second_identity,
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=GuidelineActionClass.FOOD_AVOIDANCE,
        evidence_key=second.evidence_key,
        assessment_artifact_ref=second.assessment_artifact_ref,
        selection_projection_sha256=compute_production_guideline_evidence_selection_hash(second),
        action_text_sha256=hashlib.sha256(FOOD_AVOIDANCE_TEXT.encode()).hexdigest(),
    )
    second_citation = replace(citation_draft_for(second), retrieval_receipt_ref=second.retrieval_receipt_ref)
    second_claim = GuidelineClaimDraft(
        claim_key="claim-food-2",
        medication_identity=second_identity,
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=GuidelineActionClass.FOOD_AVOIDANCE,
        action_text=SensitiveText(FOOD_AVOIDANCE_TEXT),
        citations=(second_citation,),
    )
    first_evidence = production_evidence_set(first)
    second_evidence = production_evidence_set(second)
    aggregate = GuideAggregateEvidence(
        (
            GuideAggregateEvidenceRun(
                first_request.medication_identities[0], handoff_fixture.uuid_of("a"), object(), first_evidence
            ),  # type: ignore[arg-type]
            GuideAggregateEvidenceRun(second_identity, handoff_fixture.uuid_of("b"), object(), second_evidence),  # type: ignore[arg-type]
        )
    )
    first_claim = first_request.draft.claims[0]
    first_claim = replace(
        first_claim,
        citations=(replace(first_claim.citations[0], retrieval_receipt_ref=first.retrieval_receipt_ref),),
    )
    outcome = finalize_guideline_card(
        replace(
            first_request,
            medication_identities=(first_request.medication_identities[0], second_identity),
            evidence=aggregate,
            draft=replace(first_request.draft, claims=(first_claim, second_claim)),
            approved_evidence_bindings=(first_request.approved_evidence_bindings[0], second_binding),
        )
    )

    assert outcome.status is GuidelineCardStatus.GENERATED
    assert outcome.card is not None
    assert tuple(citation.source_snapshot_id for claim in outcome.card.claims for citation in claim.citations) == (
        first.source_snapshot_id,
        second.source_snapshot_id,
    )


def test_aggregate_evidence_rejects_ambiguous_child_selection() -> None:
    request = valid_request()
    selection = production_selection()
    evidence = production_evidence_set(selection)
    aggregate = GuideAggregateEvidence(
        (
            GuideAggregateEvidenceRun(
                request.medication_identities[0], handoff_fixture.uuid_of("a"), object(), evidence
            ),  # type: ignore[arg-type]
            GuideAggregateEvidenceRun(
                request.medication_identities[0], handoff_fixture.uuid_of("b"), object(), evidence
            ),  # type: ignore[arg-type]
        )
    )
    citation = replace(request.draft.claims[0].citations[0], retrieval_receipt_ref=selection.retrieval_receipt_ref)

    outcome = finalize_guideline_card(
        replace(
            request,
            evidence=aggregate,
            draft=replace(request.draft, claims=(replace(request.draft.claims[0], citations=(citation,)),)),
        )
    )

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED


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


def test_valid_card_binds_each_claim_to_medication_and_production_evidence_citation() -> None:
    request = valid_request()
    selected = request.evidence.selections[0]
    outcome = finalize_guideline_card(request)

    assert outcome.status is GuidelineCardStatus.GENERATED
    assert outcome.reason is GuidelineCardReason.CARD_GENERATED
    assert outcome.fallback_code is None
    assert outcome.fallback is None
    assert outcome.card is not None
    assert outcome.card.claims[0].medication_identity == medication()
    assert outcome.card.claims[0].citations[0].assessment_artifact_ref == selected.assessment_artifact_ref
    assert outcome.card.claims[0].citations[0].locator == selected.locator
    assert outcome.card.claims[0].citations[0].source_snapshot_id == selected.source_snapshot_id
    assert outcome.card.claims[0].citations[0].source_snapshot_member_id == selected.source_snapshot_member_id
    assert outcome.card.claims[0].citations[0].source_code == selected.source_code
    assert outcome.card.claims[0].citations[0].source_type.value == "LIFESTYLE_GUIDELINE"
    assert outcome.card.provenance.prompt_ref == request.provenance.prompt_ref
    assert outcome.card.provenance.model_ref == request.provenance.model_ref
    assert outcome.card.provenance.parser_ref == request.provenance.parser_ref
    assert outcome.card.provenance.validator_ref == request.provenance.validator_ref
    assert outcome.card.provenance.guideline_policy_ref == request.policy.artifact_ref


def test_card_accepts_evidence_projected_from_an_actual_760_handoff() -> None:
    """The production Card path consumes a real #760 handoff projection, not a Gate outcome."""
    request = valid_request()
    assert request.draft is not None
    action_text = request.draft.claims[0].action_text.reveal()
    handoff = handoff_fixture.verified_handoff(handoff_fixture.verified_selection(text=action_text))
    evidence_set = project_guideline_evidence_from_handoff(handoff)
    evidence = evidence_set.selections[0]
    claim = replace(request.draft.claims[0], citations=(citation_draft_for(evidence),))
    binding = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@synthetic-760",
        medication_identity=claim.medication_identity,
        scope=claim.scope,
        action_class=claim.action_class,
        evidence_key=evidence.evidence_key,
        assessment_artifact_ref=evidence.assessment_artifact_ref,
        selection_projection_sha256=compute_production_guideline_evidence_selection_hash(evidence),
        action_text_sha256=hashlib.sha256(action_text.encode()).hexdigest(),
    )

    outcome = finalize_guideline_card(
        replace(
            request,
            evidence=evidence_set,
            draft=replace(request.draft, claims=(claim,)),
            evaluated_at=evidence_set.evaluated_at,
            approved_evidence_bindings=(binding,),
        )
    )

    assert outcome.status is GuidelineCardStatus.GENERATED


def test_production_request_has_no_evidence_status_input_to_map() -> None:
    """The retired RAG-14 Gate statuses are not reachable through a production request.

    `EVIDENCE_INSUFFICIENT`, `EVIDENCE_CONFLICTED` and `EVIDENCE_STALE` were Gate
    outcomes. `ProductionGuidelineEvidenceSet` carries no status field at all, because
    #760 fails closed before RAG-15 runs: an unusable handoff produces no production
    evidence for this kernel to interpret. The enum members stay defined, and the
    approved fallback copy for each remains required, but mapping an upstream handoff
    rejection onto a public fallback belongs to the #180 runtime, not to this kernel.
    """
    fields = set(ProductionGuidelineEvidenceSet.__dataclass_fields__)

    assert "evidence_status" not in fields
    assert "execution_status" not in fields
    assert "reason" not in fields
    assert {
        GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
        GuidelineFallbackCode.CONFLICTING_EVIDENCE,
    } <= {item.code for item in valid_request().approved_fallbacks}


def test_structurally_unusable_production_evidence_is_a_validation_failure() -> None:
    request = valid_request()
    evidence = request.evidence.selections[0]
    duplicate_coordinate = replace(evidence, evidence_key="knowledge:guideline-2")

    for broken in (
        replace(request.evidence, selections=()),
        replace(request.evidence, handoff_sha256="not-a-sha256"),
        replace(request.evidence, selections=(evidence, evidence)),
        replace(request.evidence, selections=(evidence, duplicate_coordinate)),
        replace(request.evidence, evaluated_at=datetime(2036, 9, 10, 3, 0, tzinfo=UTC)),
    ):
        outcome = finalize_guideline_card(replace(request, evidence=broken))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.reason is GuidelineCardReason.VALIDATION_FAILED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
        assert outcome.card is None


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
        "잠자기 전에 이 약을 드세요.",
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


def test_verifier_cannot_mutate_approved_fallback_after_snapshot() -> None:
    request = valid_request()
    source = next(item for item in request.approved_fallbacks if item.code is GuidelineFallbackCode.PROVIDER_TIMEOUT)
    original = source.text.reveal()

    class MutatingApprovalVerifier(SyntheticApprovalVerifier):
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            object.__setattr__(
                source.text,
                "_SensitiveText__value",
                "이 약의 복용을 중단하세요.",
            )
            return super().verify(artifact_ref)

    outcome = _finalize_guideline_card(
        replace(request, draft=None, generation_failure=GuidelineGenerationFailure.PROVIDER_TIMEOUT),
        approval_verifier=MutatingApprovalVerifier(),
    )

    assert outcome.status is GuidelineCardStatus.NO_RESULT
    assert outcome.fallback is not None
    assert outcome.fallback.text.reveal() == original
    assert outcome.fallback.artifact_ref == source.artifact_ref


def test_verifier_cannot_mutate_original_policy_after_request_snapshot() -> None:
    request = valid_request()
    assert request.draft is not None
    original_claim = request.draft.claims[0]
    claims = tuple(replace(original_claim, claim_key=f"claim-food-{index}") for index in range(5))
    request = replace(request, draft=replace(request.draft, claims=claims))
    expanded_policy = VersionedGuidelinePolicy.create(
        request.policy.artifact_ref.artifact_code,
        request.policy.artifact_ref.version,
        maximum_claims=5,
        uncertainty_text_sha256=request.policy.uncertainty_text_sha256,
        consultation_text_sha256=request.policy.consultation_text_sha256,
    )

    class MutatingPolicyVerifier(SyntheticApprovalVerifier):
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            object.__setattr__(request.policy, "maximum_claims", expanded_policy.maximum_claims)
            object.__setattr__(request.policy, "artifact_ref", expanded_policy.artifact_ref)
            return super().verify(artifact_ref)

    outcome = _finalize_guideline_card(request, approval_verifier=MutatingPolicyVerifier())

    assert request.policy.maximum_claims == 5
    assert request.policy.artifact_ref == expanded_policy.artifact_ref
    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason is GuidelineCardReason.VALIDATION_FAILED
    assert outcome.card is None


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


def test_policy_approved_forbidden_notice_copy_is_rejected() -> None:
    request = valid_request()
    assert request.draft is not None
    unsafe_texts = (
        "이 약의 복용을 중단하세요.",
        "용량을 늘리세요. 약사와 상담하세요.",
        "식사 후 이 약을 반 알씩 드세요.",
        "식사 전에 이 약을 드세요.",
        "잠자기 전에 이 약을 드세요.",
        "음주할 때 다른 약으로 교체하세요.",
        "음식 주의 사항으로 고혈압으로 보여요.",
    )
    cases = tuple(
        draft
        for text in unsafe_texts
        for draft in (
            replace(request.draft, uncertainty_text=SensitiveText(text)),
            replace(request.draft, consultation_text=SensitiveText(f"{text} 약사와 상담하세요.")),
        )
    )

    for draft in cases:
        policy = VersionedGuidelinePolicy.create(
            "guideline-policy",
            "guideline-policy@synthetic-unsafe-notice",
            maximum_claims=request.policy.maximum_claims,
            uncertainty_text_sha256=hashlib.sha256(draft.uncertainty_text.reveal().encode()).hexdigest(),
            consultation_text_sha256=hashlib.sha256(draft.consultation_text.reveal().encode()).hexdigest(),
        )
        outcome = finalize_guideline_card(replace(request, draft=draft, policy=policy))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.card is None


def test_self_hashed_forbidden_fallback_paraphrases_are_rejected() -> None:
    request = valid_request()
    forbidden_texts = (
        "식사 후 이 약을 반 알씩 드세요.",
        "식사 전에 이 약을 드세요.",
        "음주할 때 다른 약으로 교체하세요.",
        "음식 주의 사항으로 고혈압으로 보여요.",
    )

    for text in forbidden_texts:
        unsafe = ApprovedGuidelineFallback.create(
            "guideline-fallback",
            "guideline-fallback@synthetic-unsafe-paraphrase",
            code=GuidelineFallbackCode.PROVIDER_TIMEOUT,
            text=SensitiveText(text),
        )
        fallbacks = tuple(
            unsafe if item.code is GuidelineFallbackCode.PROVIDER_TIMEOUT else item
            for item in request.approved_fallbacks
        )
        outcome = finalize_guideline_card(replace(request, approved_fallbacks=fallbacks))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback is None


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


def test_mutated_production_evidence_content_is_rejected() -> None:
    """content_text must still hash to content_sha256 at the Card boundary."""
    request = valid_request()
    evidence = request.evidence.selections[0]
    tampered = replace(evidence, content_text=SensitiveText("변조된 합성 근거"))

    outcome = finalize_guideline_card(replace(request, evidence=replace(request.evidence, selections=(tampered,))))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_replaced_production_source_coordinate_cannot_reuse_the_original_binding() -> None:
    """Swapping the Source coordinate breaks the production selection hash binding."""
    request = valid_request()
    assert request.draft is not None
    evidence = request.evidence.selections[0]
    attacker = replace(
        evidence,
        source_snapshot_id=handoff_fixture.uuid_of("9"),
        source_snapshot_member_id=handoff_fixture.uuid_of("9"),
        source_version="api:" + "2" * 64,
        locator="$.attacker.injected",
    )
    changed_draft = replace(
        request.draft,
        claims=(replace(request.draft.claims[0], citations=(citation_draft_for(attacker),)),),
    )

    outcome = finalize_guideline_card(
        replace(
            request,
            evidence=replace(request.evidence, selections=(attacker,)),
            draft=changed_draft,
        )
    )

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.card is None


def test_replayed_card_evaluation_against_older_production_evidence_is_rejected() -> None:
    request = valid_request()
    replayed = replace(request, evaluated_at=datetime(2036, 9, 10, 3, 0, tzinfo=UTC))

    outcome = finalize_guideline_card(replayed)

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_malformed_production_evidence_selection_is_rejected() -> None:
    request = valid_request()
    evidence = request.evidence.selections[0]

    for malformed in (
        replace(evidence, evidence_key=""),
        replace(evidence, source_code=" leading-space"),
        replace(evidence, content_sha256="not-a-sha256"),
        replace(evidence, assessment_artifact_ref=replace(evidence.assessment_artifact_ref, content_sha256="bad")),
    ):
        outcome = finalize_guideline_card(replace(request, evidence=replace(request.evidence, selections=(malformed,))))
        assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
        assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED


def test_legacy_gate_selection_hash_is_not_accepted_as_a_production_binding() -> None:
    """A binding carrying the retired RAG-14 hash must not validate in production."""
    from ai_worker.tasks.rag.evidence_gate import canonical_gate_selection_hash
    from ai_worker.tests.rag import test_evidence_gate as rag14_fixture

    request = valid_request()
    assert request.draft is not None
    evidence = request.evidence.selections[0]
    legacy_hash = canonical_gate_selection_hash(rag14_fixture.selection(text=FOOD_AVOIDANCE_TEXT))
    current = request.approved_evidence_bindings[0]
    legacy_binding = ApprovedGuidelineEvidenceBinding.create(
        current.artifact_ref.artifact_code,
        current.artifact_ref.version,
        medication_identity=current.medication_identity,
        scope=current.scope,
        action_class=current.action_class,
        evidence_key=current.evidence_key,
        assessment_artifact_ref=current.assessment_artifact_ref,
        selection_projection_sha256=legacy_hash,
        action_text_sha256=current.action_text_sha256,
    )

    assert legacy_hash != compute_production_guideline_evidence_selection_hash(evidence)
    outcome = finalize_guideline_card(replace(request, approved_evidence_bindings=(legacy_binding,)))

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
    assert outcome.card is None


def test_claim_scope_must_match_approved_medication_evidence_binding() -> None:
    request = valid_request()
    selected = request.evidence.selections[0]
    evidence_key = selected.evidence_key
    matching = ApprovedGuidelineEvidenceBinding.create(
        "guideline-evidence-binding",
        "guideline-evidence-binding@synthetic-1",
        medication_identity=medication(),
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=GuidelineActionClass.FOOD_AVOIDANCE,
        evidence_key=evidence_key,
        assessment_artifact_ref=selected.assessment_artifact_ref,
        selection_projection_sha256=compute_production_guideline_evidence_selection_hash(selected),
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
        selection_projection_sha256=compute_production_guideline_evidence_selection_hash(selected),
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
    original_assessment = outcome.card.claims[0].citations[0].assessment_artifact_ref.artifact_code
    artifact_ref = outcome.card.artifact_ref

    object.__setattr__(request.medication_identities[0], "canonical_code", "TAMPERED")
    object.__setattr__(request.provenance.prompt_ref, "artifact_code", "tampered-prompt")
    selected = request.evidence.selections[0]
    object.__setattr__(selected.assessment_artifact_ref, "artifact_code", "tampered-assessment")

    assert outcome.card.claims[0].medication_identity.canonical_code == original_identity
    assert outcome.card.provenance.prompt_ref.artifact_code == original_prompt
    assert outcome.card.claims[0].citations[0].assessment_artifact_ref.artifact_code == original_assessment
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


def test_malformed_approval_response_is_a_dependency_failure_without_fallback() -> None:
    request = valid_request()

    class RejectingVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> object:
            return object()

    outcome = _finalize_guideline_card(request, approval_verifier=RejectingVerifier())  # type: ignore[arg-type]

    assert outcome.status is GuidelineCardStatus.NO_RESULT
    assert outcome.reason is GuidelineCardReason.DEPENDENCY_UNAVAILABLE
    assert outcome.fallback_code is GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE
    assert outcome.fallback is None


def test_explicit_approval_rejection_has_no_unverified_fallback() -> None:
    request = valid_request()

    class RejectingVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationFailure:
            return GuidelineApprovalVerificationFailure()

    outcome = _finalize_guideline_card(request, approval_verifier=RejectingVerifier())

    assert outcome.status is GuidelineCardStatus.VALIDATION_REJECTED
    assert outcome.reason is GuidelineCardReason.VALIDATION_FAILED
    assert outcome.fallback_code is GuidelineFallbackCode.VALIDATION_FAILED
    assert outcome.fallback is None


def test_approval_verifier_dependency_failures_have_no_unverified_fallback() -> None:
    request = valid_request()

    class RaisingVerifier:
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            raise RuntimeError("synthetic verifier outage")

    class MutatingInputVerifier(SyntheticApprovalVerifier):
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            object.__setattr__(artifact_ref, "artifact_code", "mutated")
            return super().verify(artifact_ref)

    class MismatchedSuccessVerifier(SyntheticApprovalVerifier):
        def verify(self, artifact_ref: ImmutableArtifactRef) -> GuidelineApprovalVerificationSuccess:
            return GuidelineApprovalVerificationSuccess(
                artifact_ref=artifact("different-approved-artifact"),
                verifier_artifact_ref=artifact("guideline-approval-verifier"),
            )

    for verifier in (RaisingVerifier(), MutatingInputVerifier(), MismatchedSuccessVerifier()):
        outcome = _finalize_guideline_card(request, approval_verifier=verifier)
        assert outcome.status is GuidelineCardStatus.NO_RESULT
        assert outcome.reason is GuidelineCardReason.DEPENDENCY_UNAVAILABLE
        assert outcome.fallback_code is GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE
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
