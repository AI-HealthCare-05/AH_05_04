"""#781 Request-scoped Guideline approval authority regression tests.

The verifier approves exactly two ref classes: the static policy/fallback pins the
#729 READY outcome carries, and the dynamic binding refs it recomputes itself from
the authoritative request inputs. It never trusts the issuer's binding tuple, and
`approval_pack_ref`/`candidate_ref` are provenance, not approval targets.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import ai_worker.tasks.rag.guideline_evidence_binding_authority as binding_authority
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_runtime_preflight import (
    GuideRuntimePreflightDecision,
    GuideRuntimePreflightOutcome,
    GuideRuntimePreflightReason,
    GuideRuntimePreflightRequest,
    preflight_guide_runtime,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineEvidenceBinding,
    GuidelineApprovalVerificationFailure,
    GuidelineApprovalVerificationSuccess,
    GuidelineApprovalVerifierPort,
    GuidelineCardReason,
    GuidelineCardRequest,
    GuidelineCardStatus,
    GuidelineScope,
    create_canonical_card_draft,
    create_canonical_claim_draft,
    finalize_guideline_card,
)
from ai_worker.tasks.rag.guideline_evidence_binding_authority import (
    GUIDELINE_APPROVAL_VERIFIER_REF,
    GuidelineAuthorityFailureReason,
    build_request_scoped_guideline_authority,
)
from ai_worker.tests.rag.test_guide_runtime_preflight import build_approved_pack, make_generator
from ai_worker.tests.rag.test_guideline_approval_pack import make_fallbacks, make_policy
from ai_worker.tests.rag.test_guideline_card import (
    EVALUATED_AT,
    citation_draft_for,
    medication,
    production_evidence_set,
    production_selection,
)
from ai_worker.tests.rag.test_guideline_evidence_binding import food_claim, second_medication


def ready_outcome() -> GuideRuntimePreflightOutcome:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, decision_verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    outcome = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=make_generator(),
        decision_verifier=decision_verifier,
    )
    assert outcome.decision is GuideRuntimePreflightDecision.READY
    return outcome


def authority_for(preflight: GuideRuntimePreflightOutcome | None = None, *, draft=None, medications=None):
    evidence = production_selection()
    return (
        evidence,
        build_request_scoped_guideline_authority(
            preflight if preflight is not None else ready_outcome(),
            evidence=production_evidence_set(evidence),
            medication_identities=medications if medications is not None else (medication(),),
            draft=draft if draft is not None else create_canonical_card_draft((food_claim(evidence),)),
        ),
    )


def assert_verified(verifier, ref: ImmutableArtifactRef) -> None:
    response = verifier.verify(ref)
    assert type(response) is GuidelineApprovalVerificationSuccess
    assert response.artifact_ref == ref
    assert response.verifier_artifact_ref == GUIDELINE_APPROVAL_VERIFIER_REF


def assert_rejected(verifier, ref: ImmutableArtifactRef) -> None:
    assert type(verifier.verify(ref)) is GuidelineApprovalVerificationFailure


# --- Factory root-of-trust boundary -------------------------------------------


def test_ready_preflight_outcome_builds_an_authority() -> None:
    _, outcome = authority_for()

    assert outcome.reason is None
    assert outcome.authority is not None
    assert len(outcome.authority.bindings) == 1


def test_blocked_preflight_outcome_cannot_build_an_authority() -> None:
    blocked = GuideRuntimePreflightOutcome(
        decision=GuideRuntimePreflightDecision.BLOCKED,
        reason=GuideRuntimePreflightReason.POLICY_REF_MISMATCH,
        ready_context=None,
    )
    _, outcome = authority_for(blocked)

    assert outcome.authority is None
    assert outcome.reason is GuidelineAuthorityFailureReason.PREFLIGHT_NOT_READY


def test_ready_decision_without_ready_context_cannot_build_an_authority() -> None:
    _, outcome = authority_for(
        GuideRuntimePreflightOutcome(
            decision=GuideRuntimePreflightDecision.READY,
            reason=None,
            ready_context=None,
        )
    )

    assert outcome.authority is None
    assert outcome.reason is GuidelineAuthorityFailureReason.PREFLIGHT_NOT_READY


def test_ready_context_alone_is_not_a_preflight_outcome() -> None:
    ready = ready_outcome()
    assert ready.ready_context is not None
    evidence = production_selection()
    outcome = build_request_scoped_guideline_authority(
        ready.ready_context,  # type: ignore[arg-type]
        evidence=production_evidence_set(evidence),
        medication_identities=(medication(),),
        draft=create_canonical_card_draft((food_claim(evidence),)),
    )

    assert outcome.authority is None
    assert outcome.reason is GuidelineAuthorityFailureReason.PREFLIGHT_NOT_READY


def test_ready_outcome_carrying_a_stale_reason_cannot_build_an_authority() -> None:
    ready = ready_outcome()
    _, outcome = authority_for(replace(ready, reason=GuideRuntimePreflightReason.REQUEST_INVALID))

    assert outcome.authority is None
    assert outcome.reason is GuidelineAuthorityFailureReason.PREFLIGHT_NOT_READY


def test_failed_binding_derivation_propagates_its_reason_and_builds_no_authority() -> None:
    evidence = production_selection()
    _, outcome = authority_for(draft=create_canonical_card_draft((food_claim(evidence, identity=second_medication()),)))

    assert outcome.authority is None
    assert outcome.reason is GuidelineAuthorityFailureReason.MEDICATION_NOT_PINNED


# --- Static pin membership ----------------------------------------------------


def test_ready_policy_and_fallback_pins_verify() -> None:
    ready = ready_outcome()
    assert ready.ready_context is not None
    _, outcome = authority_for(ready)
    assert outcome.authority is not None

    assert_verified(outcome.authority.approval_verifier, ready.ready_context.policy_ref)
    for fallback_ref in ready.ready_context.fallback_refs:
        assert_verified(outcome.authority.approval_verifier, fallback_ref)


def test_approval_pack_ref_and_candidate_ref_are_not_approval_targets() -> None:
    ready = ready_outcome()
    assert ready.ready_context is not None
    _, outcome = authority_for(ready)
    assert outcome.authority is not None

    assert_rejected(outcome.authority.approval_verifier, ready.ready_context.approval_pack_ref)
    assert_rejected(outcome.authority.approval_verifier, ready.ready_context.candidate_ref)


def test_unknown_ref_is_rejected() -> None:
    _, outcome = authority_for()
    assert outcome.authority is not None

    assert_rejected(
        outcome.authority.approval_verifier,
        ImmutableArtifactRef("guideline-policy", "guideline-policy@unknown", "c" * 64),
    )


def test_non_artifact_ref_input_is_rejected() -> None:
    _, outcome = authority_for()
    assert outcome.authority is not None

    assert_rejected(outcome.authority.approval_verifier, "guideline-policy")  # type: ignore[arg-type]


# --- Dynamic binding verification independence --------------------------------


def test_recomputed_binding_ref_verifies() -> None:
    _, outcome = authority_for()
    assert outcome.authority is not None

    assert_verified(outcome.authority.approval_verifier, outcome.authority.bindings[0].artifact_ref)


def test_tampered_binding_ref_is_rejected() -> None:
    _, outcome = authority_for()
    assert outcome.authority is not None
    tampered = replace(outcome.authority.bindings[0].artifact_ref, content_sha256="d" * 64)

    assert_rejected(outcome.authority.approval_verifier, tampered)


def test_verifier_does_not_trust_a_binding_forged_into_the_issuer_tuple() -> None:
    evidence, outcome = authority_for()
    assert outcome.authority is not None
    forged = ApprovedGuidelineEvidenceBinding.create(
        outcome.authority.bindings[0].artifact_ref.artifact_code,
        outcome.authority.bindings[0].artifact_ref.version,
        medication_identity=second_medication(),
        scope=GuidelineScope.FOOD_CAUTION,
        action_class=outcome.authority.bindings[0].action_class,
        evidence_key=evidence.evidence_key,
        assessment_artifact_ref=evidence.assessment_artifact_ref,
        selection_projection_sha256=outcome.authority.bindings[0].selection_projection_sha256,
        action_text_sha256=outcome.authority.bindings[0].action_text_sha256,
    )
    forged_authority = replace(outcome.authority, bindings=(*outcome.authority.bindings, forged))

    assert_rejected(forged_authority.approval_verifier, forged.artifact_ref)
    assert_verified(forged_authority.approval_verifier, outcome.authority.bindings[0].artifact_ref)


# --- Verifier identity --------------------------------------------------------


def test_verifier_identity_is_stable_across_different_requests() -> None:
    first_evidence, first = authority_for()
    second_evidence = production_selection(
        evidence_key="knowledge:guideline-2",
        locator="$.items[0].atpnQesitm",
        source_version="api:" + "2" * 64,
    )
    second = build_request_scoped_guideline_authority(
        ready_outcome(),
        evidence=production_evidence_set(second_evidence),
        medication_identities=(second_medication(),),
        draft=create_canonical_card_draft((food_claim(second_evidence, identity=second_medication()),)),
    )
    assert first.authority is not None and second.authority is not None

    assert first.authority.bindings[0].artifact_ref != second.authority.bindings[0].artifact_ref
    left = first.authority.approval_verifier.verify(first.authority.bindings[0].artifact_ref)
    right = second.authority.approval_verifier.verify(second.authority.bindings[0].artifact_ref)
    assert type(left) is GuidelineApprovalVerificationSuccess
    assert type(right) is GuidelineApprovalVerificationSuccess
    assert left.verifier_artifact_ref == right.verifier_artifact_ref == GUIDELINE_APPROVAL_VERIFIER_REF
    assert first_evidence.evidence_key != second_evidence.evidence_key


def test_verifier_identity_binds_explicit_contract_semantics() -> None:
    assert GUIDELINE_APPROVAL_VERIFIER_REF.artifact_code == "request-scoped-guideline-approval-verifier"
    assert GUIDELINE_APPROVAL_VERIFIER_REF.version == "request-scoped-guideline-approval-verifier-v1"
    assert GUIDELINE_APPROVAL_VERIFIER_REF.content_sha256 != hashlib.sha256(b"guideline-verifier-v1").hexdigest()


# --- Pure finalizer composition -----------------------------------------------


def test_ready_inputs_compose_into_a_generated_card_without_io() -> None:
    policy = make_policy()
    fallbacks = make_fallbacks()
    pack, decision_verifier = build_approved_pack(policy=policy, fallbacks=fallbacks)
    preflight = preflight_guide_runtime(
        GuideRuntimePreflightRequest(approval_pack=pack, policy=policy, fallbacks=fallbacks),
        generator=make_generator(),
        decision_verifier=decision_verifier,
    )
    assert preflight.ready_context is not None

    evidence = production_selection()
    evidence_set = production_evidence_set(evidence)
    identity = medication()
    draft = create_canonical_card_draft(
        (
            create_canonical_claim_draft(
                claim_key="claim-food-1",
                medication_identity=identity,
                scope=GuidelineScope.FOOD_CAUTION,
                citations=(citation_draft_for(evidence),),
            ),
        )
    )
    outcome = build_request_scoped_guideline_authority(
        preflight,
        evidence=evidence_set,
        medication_identities=(identity,),
        draft=draft,
    )
    assert outcome.authority is not None

    card_outcome = finalize_guideline_card(
        GuidelineCardRequest(
            medication_identities=(identity,),
            evidence=evidence_set,
            draft=draft,
            generation_failure=None,
            policy=policy,
            provenance=preflight.ready_context.generation_provenance,
            approved_fallbacks=fallbacks,
            evaluated_at=EVALUATED_AT,
            approved_evidence_bindings=outcome.authority.bindings,
        ),
        approval_verifier=outcome.authority.approval_verifier,
    )

    assert card_outcome.status is GuidelineCardStatus.GENERATED
    assert card_outcome.reason is GuidelineCardReason.CARD_GENERATED
    assert card_outcome.card is not None
    citation = card_outcome.card.claims[0].citations[0]
    assert citation.guideline_evidence_binding_ref == outcome.authority.bindings[0].artifact_ref
    assert citation.guideline_evidence_binding_verifier_ref == GUIDELINE_APPROVAL_VERIFIER_REF


def test_concrete_verifier_is_not_part_of_the_public_api_surface() -> None:
    """The factory is the only supported construction path for a request-scoped verifier."""
    assert "RequestScopedGuidelineApprovalVerifier" not in binding_authority.__all__
    assert not hasattr(binding_authority, "RequestScopedGuidelineApprovalVerifier")
    assert "build_request_scoped_guideline_authority" in binding_authority.__all__


def test_authority_exposes_the_verifier_through_the_existing_port() -> None:
    _, outcome = authority_for()
    assert outcome.authority is not None
    verifier: GuidelineApprovalVerifierPort = outcome.authority.approval_verifier

    assert callable(verifier.verify)
