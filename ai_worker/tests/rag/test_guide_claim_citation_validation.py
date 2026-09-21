"""#794 Guideline Card -> Claim/Citation validation handoff regressions.

This seam projects an already verified Card onto an already verified evidence
handoff and then calls an already merged pure kernel, so these tests do not
re-verify #729 preflight, #760 assembly, #774 projection, #781 derivation, the #179
Card finalizer or the RAG-16 validator; those suites own their own validation. What
is verified here is that only a GENERATED Card enters, that Card and upstream handoff
must exact-match, that a claim's support artifact binds *every* citation it carries
rather than an arbitrary one, that receipts count claims and not citations, and that
the run reaches `ValidatedCitationSelection` through the existing kernel.
"""

# mypy: disable-error-code="type-var,union-attr"
from __future__ import annotations

import ast
import pathlib
import unicodedata
from dataclasses import replace

import pytest

from ai_worker.tasks.rag import guide_claim_citation_validation
from ai_worker.tasks.rag.claim_citation_validator import (
    CandidateValidationDecision,
    CandidateValidationExecutionStatus,
    CandidateValidationReason,
    CitationSourceType,
    ClaimKind,
    ClaimSupportStatus,
    LifestyleGuidelineEvidenceRef,
    canonical_claim_support_projection_hash,
)
from ai_worker.tasks.rag.guide_claim_citation_validation import (
    GUIDE_CLAIM_CITATION_TARGET_KIND,
    GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_REF,
    GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_CODE,
    GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_VERSION,
    GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF,
    GuideClaimCitationDecision,
    GuideClaimCitationStage,
    GuideClaimCitationValidationOutcome,
    GuideClaimCitationValidationRequest,
    compute_guide_claim_citation_validator_policy_ref,
    compute_guideline_claim_support_assessment_ref,
    compute_guideline_claim_support_verifier_artifact_ref,
    run_guide_claim_citation_validation,
)
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOutcome,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardStatus,
    GuidelineGenerationFailure,
    GuidelineScope,
    create_canonical_card_draft,
    create_canonical_claim_draft,
)
from ai_worker.tasks.rag.guideline_generator import GuidelineGenerationRequest
from ai_worker.tasks.rag.guideline_production_evidence import ProductionGuidelineEvidenceSet

# #787이 이미 확정한 production chain fixture를 그대로 재사용한다. 같은 합성 체인을
# 여기서 다시 만들면 두 벌의 fixture가 서로 어긋날 수 있다.
from ai_worker.tests.rag.test_guide_generation_card_orchestration import (
    MEDICATION,
    DraftFromEvidenceGenerator,
    RecordingGenerator,
    approved_run,
)
from ai_worker.tests.rag.test_guideline_card import citation_draft_for


class MultiCitationGenerator(RecordingGenerator):
    """One claim citing two production selections.

    The multi-citation claim is the case this slice exists for: a claim with two
    citations must still yield one support receipt, and its support artifact must
    bind both citations.
    """

    def __init__(self, *, reverse: bool = False) -> None:
        super().__init__(None)
        self._reverse = reverse

    async def generate(self, request: GuidelineGenerationRequest):
        assert isinstance(request.evidence, ProductionGuidelineEvidenceSet)
        self._result = _multi_citation_draft(request.evidence, reverse=self._reverse)
        return await super().generate(request)


class TwoClaimGenerator(RecordingGenerator):
    """Two claims, one citation each, over distinct scopes."""

    def __init__(self) -> None:
        super().__init__(None)

    async def generate(self, request: GuidelineGenerationRequest):
        assert isinstance(request.evidence, ProductionGuidelineEvidenceSet)
        selections = request.evidence.selections
        self._result = create_canonical_card_draft(
            (
                create_canonical_claim_draft(
                    claim_key="claim-1",
                    medication_identity=MEDICATION,
                    scope=GuidelineScope.FOOD_CAUTION,
                    citations=(citation_draft_for(selections[0]),),
                ),
                create_canonical_claim_draft(
                    claim_key="claim-2",
                    medication_identity=MEDICATION,
                    scope=GuidelineScope.DAILY_ACTIVITY,
                    citations=(citation_draft_for(selections[1]),),
                ),
            )
        )
        return await super().generate(request)


def _multi_citation_draft(evidence: ProductionGuidelineEvidenceSet, *, reverse: bool = False):
    selections = list(evidence.selections[:2])
    assert len(selections) == 2
    if reverse:
        selections.reverse()
    return create_canonical_card_draft(
        (
            create_canonical_claim_draft(
                claim_key="claim-1",
                medication_identity=MEDICATION,
                scope=GuidelineScope.FOOD_CAUTION,
                citations=tuple(citation_draft_for(item) for item in selections),
            ),
        )
    )


def single_citation_outcome() -> GuideGenerationCardOutcome:
    outcome = approved_run(DraftFromEvidenceGenerator())
    assert outcome.card_outcome is not None
    assert outcome.card_outcome.status is GuidelineCardStatus.GENERATED
    return outcome


def multi_citation_outcome(*, reverse: bool = False) -> GuideGenerationCardOutcome:
    outcome = approved_run(MultiCitationGenerator(reverse=reverse))
    assert outcome.card_outcome is not None
    assert outcome.card_outcome.status is GuidelineCardStatus.GENERATED
    return outcome


def two_claim_outcome() -> GuideGenerationCardOutcome:
    outcome = approved_run(TwoClaimGenerator())
    assert outcome.card_outcome is not None
    assert outcome.card_outcome.status is GuidelineCardStatus.GENERATED
    return outcome


def run(guide_outcome: GuideGenerationCardOutcome) -> GuideClaimCitationValidationOutcome:
    return run_guide_claim_citation_validation(GuideClaimCitationValidationRequest(guide_outcome=guide_outcome))


def card_of(guide_outcome: GuideGenerationCardOutcome):
    assert guide_outcome.card_outcome is not None
    card = guide_outcome.card_outcome.card
    assert card is not None
    return card


def with_handoff_selection(guide_outcome: GuideGenerationCardOutcome, index: int, **changes):
    """Rewrite one upstream handoff selection, leaving the Card untouched.

    Mutating the handoff rather than the Card is what makes this a Card <-> handoff
    mismatch test: the Card is still the one the finalizer produced.
    """
    ready_inputs = guide_outcome.upstream_outcome.ready_inputs
    assert ready_inputs is not None
    handoff = ready_inputs.evidence_handoff
    selections = list(handoff.selections)
    selections[index] = replace(selections[index], **changes)
    return replace(
        guide_outcome,
        upstream_outcome=replace(
            guide_outcome.upstream_outcome,
            ready_inputs=replace(ready_inputs, evidence_handoff=replace(handoff, selections=tuple(selections))),
        ),
    )


# ==============================================================================
# A. GENERATED happy path — terminal point is ValidatedCitationSelection
# ==============================================================================


def test_generated_card_reaches_a_validated_citation_selection() -> None:
    outcome = run(single_citation_outcome())

    assert outcome.decision is GuideClaimCitationDecision.VALIDATED
    assert outcome.stopped_stage is None
    assert outcome.validation_outcome is not None
    assert outcome.validation_outcome.execution_status is CandidateValidationExecutionStatus.EVALUATED
    assert outcome.validation_outcome.decision is CandidateValidationDecision.VALIDATED
    assert outcome.validation_outcome.reasons == ()
    assert outcome.validated_selection is not None
    assert outcome.validated_selection is outcome.validation_outcome.validated_selection


def test_candidate_set_projects_the_card_semantics() -> None:
    guide_outcome = single_citation_outcome()
    card = card_of(guide_outcome)

    outcome = run(guide_outcome)

    assert outcome.candidate_set is not None
    candidate_set = outcome.candidate_set
    assert candidate_set.target.target_kind == GUIDE_CLAIM_CITATION_TARGET_KIND
    assert candidate_set.target.target_ref == card.artifact_ref.content_sha256
    assert [claim.claim_key for claim in candidate_set.claims] == [claim.claim_key for claim in card.claims]
    assert all(claim.claim_kind is ClaimKind.MEDICAL for claim in candidate_set.claims)
    assert all(claim.support_assertion.support_status is ClaimSupportStatus.SUPPORTED for claim in candidate_set.claims)
    assert [claim.display_order for claim in candidate_set.claims] == [1]
    assert all(citation.source_type is CitationSourceType.LIFESTYLE_GUIDELINE for citation in candidate_set.citations)
    assert [citation.display_order for citation in candidate_set.citations] == [1]
    assert candidate_set.generation_provenance.prompt_ref == card.provenance.prompt_ref
    assert candidate_set.generation_provenance.model_ref == card.provenance.model_ref
    assert candidate_set.generation_provenance.parser_ref == card.provenance.parser_ref
    assert candidate_set.validator_policy_ref == GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_REF


def test_execution_provenance_is_projected_from_the_upstream_handoff() -> None:
    guide_outcome = single_citation_outcome()
    ready_inputs = guide_outcome.upstream_outcome.ready_inputs
    assert ready_inputs is not None
    selections = {item.evidence_key: item for item in ready_inputs.evidence_handoff.selections}

    outcome = run(guide_outcome)

    assert outcome.candidate_set is not None
    card_citation = card_of(guide_outcome).claims[0].citations[0]
    evidence_ref = outcome.candidate_set.citations[0].evidence_ref
    assert isinstance(evidence_ref, LifestyleGuidelineEvidenceRef)
    selection = selections[card_citation.evidence_key]
    provenance = evidence_ref.execution_provenance
    assert provenance.source_code == selection.source_code
    assert provenance.source_version == selection.source_version
    assert provenance.member_kind is selection.member_kind
    assert provenance.endpoint_code == selection.endpoint_code
    assert provenance.operation_code == selection.operation_code
    assert provenance.artifact_code == selection.artifact_code
    assert provenance.artifact_version == selection.artifact_version
    assert provenance.request_source_decision_ref == selection.request_source_decision_ref
    assert provenance.request_member_decision_ref == selection.request_member_decision_ref
    # The binding artifact the Card already carries, not a synthesized Source Snapshot ref.
    assert evidence_ref.guideline_artifact_ref == card_citation.guideline_evidence_binding_ref
    assert evidence_ref.locator == card_citation.locator
    assert evidence_ref.content_sha256 == card_citation.content_sha256


# ==============================================================================
# B. Non-generated paths never produce a candidate
# ==============================================================================


def test_generation_failure_card_does_not_enter_claim_citation_validation() -> None:
    guide_outcome = approved_run(RecordingGenerator(GuidelineGenerationFailure.PROVIDER_TIMEOUT))
    assert guide_outcome.card_outcome is not None
    assert guide_outcome.card_outcome.status is not GuidelineCardStatus.GENERATED

    outcome = run(guide_outcome)

    assert outcome.decision is GuideClaimCitationDecision.STOPPED
    assert outcome.stopped_stage is GuideClaimCitationStage.CARD_NOT_ELIGIBLE
    assert outcome.candidate_set is None
    assert outcome.support_receipts is None
    assert outcome.validation_outcome is None
    assert outcome.validated_selection is None
    assert outcome.guide_outcome is guide_outcome


@pytest.mark.parametrize(
    "status",
    [
        GuidelineCardStatus.NO_RESULT,
        GuidelineCardStatus.LIMITED,
        GuidelineCardStatus.STALE,
        GuidelineCardStatus.VALIDATION_REJECTED,
    ],
)
def test_non_generated_card_status_stops_before_projection(status: GuidelineCardStatus) -> None:
    guide_outcome = single_citation_outcome()
    assert guide_outcome.card_outcome is not None
    downgraded = replace(
        guide_outcome,
        card_outcome=replace(guide_outcome.card_outcome, status=status),
    )

    outcome = run(downgraded)

    assert outcome.stopped_stage is GuideClaimCitationStage.CARD_NOT_ELIGIBLE
    assert outcome.candidate_set is None


def test_stopped_orchestration_run_stops_before_projection() -> None:
    guide_outcome = replace(
        single_citation_outcome(),
        decision=GuideGenerationCardDecision.STOPPED,
    )

    outcome = run(guide_outcome)

    assert outcome.stopped_stage is GuideClaimCitationStage.CARD_NOT_ELIGIBLE
    assert outcome.candidate_set is None


def test_missing_upstream_ready_inputs_stops_before_projection() -> None:
    guide_outcome = single_citation_outcome()
    stripped = replace(
        guide_outcome,
        upstream_outcome=replace(guide_outcome.upstream_outcome, ready_inputs=None),
    )

    outcome = run(stripped)

    assert outcome.stopped_stage is GuideClaimCitationStage.CARD_NOT_ELIGIBLE
    assert outcome.candidate_set is None


# ==============================================================================
# C. Multi-citation claim — the case an arbitrary assessment pick would break
# ==============================================================================


def test_multi_citation_claim_yields_two_citations_and_one_receipt() -> None:
    guide_outcome = multi_citation_outcome()

    outcome = run(guide_outcome)

    assert outcome.decision is GuideClaimCitationDecision.VALIDATED
    assert outcome.candidate_set is not None
    assert outcome.support_receipts is not None
    assert len(outcome.candidate_set.claims) == 1
    assert len(outcome.candidate_set.citations) == 2
    assert len(outcome.support_receipts) == 1
    assert [citation.display_order for citation in outcome.candidate_set.citations] == [1, 2]


def test_two_claims_yield_two_receipts() -> None:
    outcome = run(two_claim_outcome())

    assert outcome.decision is GuideClaimCitationDecision.VALIDATED
    assert outcome.candidate_set is not None
    assert outcome.support_receipts is not None
    assert len(outcome.candidate_set.claims) == 2
    assert len(outcome.candidate_set.citations) == 2
    assert len(outcome.support_receipts) == 2
    assert {receipt.claim_key for receipt in outcome.support_receipts} == {"claim-1", "claim-2"}


def test_claim_support_artifact_is_not_any_single_citation_assessment() -> None:
    guide_outcome = multi_citation_outcome()
    card = card_of(guide_outcome)
    citations = card.claims[0].citations
    assert len(citations) == 2

    outcome = run(guide_outcome)

    assert outcome.candidate_set is not None
    assessment_ref = outcome.candidate_set.claims[0].support_assertion.assessment_ref
    assert assessment_ref.artifact_code == GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_CODE
    assert assessment_ref.version == GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_VERSION
    assert assessment_ref not in {citation.assessment_artifact_ref for citation in citations}
    assert assessment_ref not in {citation.guideline_evidence_binding_ref for citation in citations}


def test_both_citations_are_bound_into_the_claim_support_artifact() -> None:
    """Dropping either citation must change the claim's support identity.

    This is the test a `citations[0].assessment_artifact_ref` shortcut would fail:
    such a shortcut is invariant to whatever the other citation says.
    """
    guide_outcome = multi_citation_outcome()
    card = card_of(guide_outcome)
    claim = card.claims[0]
    both = compute_guideline_claim_support_assessment_ref(card, claim)

    first_only = compute_guideline_claim_support_assessment_ref(card, replace(claim, citations=claim.citations[:1]))
    second_only = compute_guideline_claim_support_assessment_ref(card, replace(claim, citations=claim.citations[1:]))

    assert len({both, first_only, second_only}) == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_key", "evidence:tampered"),
        ("locator", "doc:MFDS-DOC-1#tampered"),
        ("content_sha256", "f" * 64),
        ("source_version", "2026-09-18"),
    ],
)
def test_changing_one_citation_fact_changes_the_claim_support_artifact(field: str, value: str) -> None:
    guide_outcome = multi_citation_outcome()
    card = card_of(guide_outcome)
    claim = card.claims[0]
    baseline = compute_guideline_claim_support_assessment_ref(card, claim)

    mutated_citations = (replace(claim.citations[0], **{field: value}), *claim.citations[1:])

    assert compute_guideline_claim_support_assessment_ref(card, replace(claim, citations=mutated_citations)) != baseline


@pytest.mark.parametrize(
    "field",
    [
        "assessment_artifact_ref",
        "guideline_evidence_binding_ref",
        "guideline_evidence_binding_verifier_ref",
    ],
)
def test_changing_one_citation_artifact_ref_changes_the_claim_support_artifact(field: str) -> None:
    guide_outcome = multi_citation_outcome()
    card = card_of(guide_outcome)
    claim = card.claims[0]
    baseline = compute_guideline_claim_support_assessment_ref(card, claim)
    original = getattr(claim.citations[0], field)
    tampered = replace(original, content_sha256="a" * 64)

    mutated_citations = (replace(claim.citations[0], **{field: tampered}), *claim.citations[1:])

    assert compute_guideline_claim_support_assessment_ref(card, replace(claim, citations=mutated_citations)) != baseline


def test_changing_the_action_text_changes_the_claim_support_artifact() -> None:
    from ai_worker.tasks.rag.evidence_retrieval import SensitiveText

    guide_outcome = multi_citation_outcome()
    card = card_of(guide_outcome)
    claim = card.claims[0]
    baseline = compute_guideline_claim_support_assessment_ref(card, claim)

    mutated = replace(claim, action_text=SensitiveText(claim.action_text.reveal() + " "))

    assert compute_guideline_claim_support_assessment_ref(card, mutated) != baseline


def test_citation_order_within_a_claim_does_not_change_the_claim_support_artifact() -> None:
    """Canonical support identity ignores citation order; display order does not.

    The assessment preimage sorts its citation entries, so one Card's claim yields the
    same support identity whichever order its citation tuple is read in. The Card is
    held fixed here on purpose: `card_artifact_ref` is part of the preimage and the
    Card's own self-hash does depend on citation order, so reversing the draft would
    change the assessment ref for that reason rather than this one.
    """
    card = card_of(multi_citation_outcome())
    claim = card.claims[0]

    reversed_claim = replace(claim, citations=tuple(reversed(claim.citations)))

    assert compute_guideline_claim_support_assessment_ref(
        card, reversed_claim
    ) == compute_guideline_claim_support_assessment_ref(card, claim)


def test_candidate_display_order_follows_the_card_and_is_never_silently_reordered() -> None:
    """The assessment canonicalization must not leak into the runtime display contract."""
    forward = run(multi_citation_outcome())
    reversed_run = run(multi_citation_outcome(reverse=True))

    assert forward.candidate_set is not None
    assert reversed_run.candidate_set is not None
    forward_keys = [citation.citation_key for citation in forward.candidate_set.citations]
    reversed_keys = [citation.citation_key for citation in reversed_run.candidate_set.citations]
    assert forward_keys != reversed_keys
    assert sorted(forward_keys) == sorted(reversed_keys)
    assert [citation.display_order for citation in reversed_run.candidate_set.citations] == [1, 2]
    assert [citation.claim_key for citation in reversed_run.candidate_set.citations] == ["claim-1", "claim-1"]


# ==============================================================================
# D. Card <-> upstream handoff exact match
# ==============================================================================


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_version", "2026-09-18"),
        ("content_sha256", "e" * 64),
        ("locator", "doc:MFDS-DOC-1#tampered"),
        ("evidence_key", "evidence:unknown"),
    ],
)
def test_handoff_coordinate_mismatch_fails_closed(field: str, value: str) -> None:
    outcome = run(with_handoff_selection(single_citation_outcome(), 0, **{field: value}))

    assert outcome.decision is GuideClaimCitationDecision.STOPPED
    assert outcome.stopped_stage is GuideClaimCitationStage.CARD_PROJECTION
    assert outcome.candidate_set is None
    assert outcome.support_receipts is None
    assert outcome.validation_outcome is None


@pytest.mark.parametrize(
    "field",
    [
        "assessment_artifact_ref",
        "eligibility_receipt_ref",
        "retrieval_receipt_ref",
        "verifier_artifact_ref",
    ],
)
def test_handoff_authority_ref_mismatch_fails_closed(field: str) -> None:
    guide_outcome = single_citation_outcome()
    ready_inputs = guide_outcome.upstream_outcome.ready_inputs
    assert ready_inputs is not None
    original = getattr(ready_inputs.evidence_handoff.selections[0], field)
    tampered = replace(original, content_sha256="b" * 64)

    outcome = run(with_handoff_selection(guide_outcome, 0, **{field: tampered}))

    assert outcome.stopped_stage is GuideClaimCitationStage.CARD_PROJECTION
    assert outcome.candidate_set is None


def test_duplicate_handoff_evidence_key_fails_closed() -> None:
    guide_outcome = single_citation_outcome()
    ready_inputs = guide_outcome.upstream_outcome.ready_inputs
    assert ready_inputs is not None
    first = ready_inputs.evidence_handoff.selections[0]

    outcome = run(with_handoff_selection(guide_outcome, 1, evidence_key=first.evidence_key))

    assert outcome.stopped_stage is GuideClaimCitationStage.CARD_PROJECTION
    assert outcome.candidate_set is None


# ==============================================================================
# E. Support receipts and the existing pure kernel
# ==============================================================================


def test_receipt_fields_bind_the_claim_and_the_fixed_verifier_identity() -> None:
    outcome = run(multi_citation_outcome())

    assert outcome.candidate_set is not None
    assert outcome.support_receipts is not None
    claim = outcome.candidate_set.claims[0]
    receipt = outcome.support_receipts[0]
    assert receipt.claim_key == claim.claim_key
    assert receipt.support_status is ClaimSupportStatus.SUPPORTED
    assert receipt.claim_text_digest == claim.text_digest
    assert receipt.assessment_ref == claim.support_assertion.assessment_ref
    assert receipt.verifier_artifact_ref == GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF
    assert receipt.projection_sha256 == canonical_claim_support_projection_hash(outcome.candidate_set, claim.claim_key)


def test_verifier_and_policy_identities_are_request_independent() -> None:
    first = run(single_citation_outcome())
    second = run(multi_citation_outcome())

    assert first.support_receipts is not None
    assert second.support_receipts is not None
    assert first.support_receipts[0].verifier_artifact_ref == second.support_receipts[0].verifier_artifact_ref
    assert compute_guideline_claim_support_verifier_artifact_ref() == GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF
    assert compute_guide_claim_citation_validator_policy_ref() == GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_REF
    assert first.candidate_set is not None
    assert second.candidate_set is not None
    assert first.candidate_set.validator_policy_ref == second.candidate_set.validator_policy_ref


def test_validated_selection_carries_the_candidate_set_and_receipts() -> None:
    outcome = run(two_claim_outcome())

    assert outcome.validated_selection is not None
    assert outcome.validated_selection.candidate_set is outcome.candidate_set
    assert outcome.support_receipts is not None
    assert set(outcome.validated_selection.support_receipts) == set(outcome.support_receipts)
    assert len(outcome.validated_selection.selection_sha256) == 64


def test_projection_is_deterministic_across_runs() -> None:
    first = run(single_citation_outcome())
    second = run(single_citation_outcome())

    assert first.candidate_set == second.candidate_set
    assert first.support_receipts == second.support_receipts
    assert first.validated_selection is not None
    assert second.validated_selection is not None
    assert first.validated_selection.selection_sha256 == second.validated_selection.selection_sha256


def test_a_foreign_request_is_rejected_rather_than_silently_projected() -> None:
    with pytest.raises(TypeError):
        run_guide_claim_citation_validation(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "guide_outcome_factory",
    [single_citation_outcome, multi_citation_outcome, two_claim_outcome],
)
def test_guide_projection_only_issues_supported_claim_assertions(guide_outcome_factory) -> None:
    """The Guide adapter's support status is structurally fixed at SUPPORTED.

    Support here is not a semantic entailment judgement. It rests on artifacts that
    were already verified upstream — the Guideline Card, its Guideline Evidence
    Binding, that binding's verifier and the #760 authoritative handoff provenance —
    and this module performs no NLI or classification of its own.

    The enum membership is asserted deliberately: the generic kernel supports four
    statuses, and pinning that list here documents that #794 produces exactly one of
    them. The other three, and therefore the kernel's MEDICAL_CLAIM_NOT_SUPPORTED and
    CLAIM_NOT_SUPPORTED branches, are unreachable through this adapter. That is a
    reachability statement about #794, not a reason to remove the branches: other
    candidate producers consume the same kernel.
    """
    assert set(ClaimSupportStatus) == {
        ClaimSupportStatus.SUPPORTED,
        ClaimSupportStatus.PARTIALLY_SUPPORTED,
        ClaimSupportStatus.CONTRADICTED,
        ClaimSupportStatus.NOT_SUPPORTED,
    }

    outcome = run(guide_outcome_factory())

    assert outcome.candidate_set is not None
    assert outcome.support_receipts is not None
    projected_statuses = {claim.support_assertion.support_status for claim in outcome.candidate_set.claims}
    assert projected_statuses == {ClaimSupportStatus.SUPPORTED}
    assert {receipt.support_status for receipt in outcome.support_receipts} == {ClaimSupportStatus.SUPPORTED}


# ==============================================================================
# F. Generic claim identity stays the existing validator's job
#
# `_project_candidate_set()` copies `GuidelineClaim.claim_key` through untouched. NFC
# form, blankness and uniqueness of a claim key are generic candidate-set properties
# that `validate_claim_citations()` already owns, and duplicating those checks in this
# adapter would give the same rule two owners that can drift apart.
#
# These are defense-in-depth boundary tests, not production-reachable scenarios. On the
# normal #179 -> #787 chain the finalizer's own draft-shape check rejects a non-NFC or
# duplicate `claim_key` first (`_bounded_nfc(claim.claim_key, 100)` and the
# `claim_key in claim_keys` guard in `guideline_card._is_valid_draft_shape()`), so no
# such draft can ever become a GENERATED Card. Both fixtures therefore rewrite the
# Card's claims directly: injecting a malformed Card is the only way to show that #794
# neither duplicates the upstream check nor silently repairs the input, and that the
# generic owner downstream still fails closed. The rewritten Card's `artifact_ref` no
# longer matches its claims, which is immaterial here — verifying the Card self-hash is
# the finalizer's job, not this module's.
# ==============================================================================


def _with_card_claims(guide_outcome: GuideGenerationCardOutcome, claims) -> GuideGenerationCardOutcome:
    assert guide_outcome.card_outcome is not None
    card = guide_outcome.card_outcome.card
    assert card is not None
    return replace(
        guide_outcome,
        card_outcome=replace(guide_outcome.card_outcome, card=replace(card, claims=claims)),
    )


def test_non_nfc_claim_key_is_rejected_by_the_existing_validator_not_by_projection() -> None:
    guide_outcome = single_citation_outcome()
    claim = card_of(guide_outcome).claims[0]
    decomposed = unicodedata.normalize("NFD", "복약-클레임-1")
    assert not unicodedata.is_normalized("NFC", decomposed)
    rewritten = _with_card_claims(guide_outcome, (replace(claim, claim_key=decomposed),))

    outcome = run(rewritten)

    # Projection ran and is preserved: the key was carried through, not normalized.
    assert outcome.candidate_set is not None
    assert outcome.candidate_set.claims[0].claim_key == decomposed
    assert outcome.support_receipts is not None
    assert len(outcome.support_receipts) == 1

    assert outcome.decision is GuideClaimCitationDecision.STOPPED
    assert outcome.stopped_stage is GuideClaimCitationStage.CLAIM_CITATION_VALIDATION
    assert outcome.validation_outcome is not None
    assert outcome.validation_outcome.execution_status is CandidateValidationExecutionStatus.VALIDATION_ERROR
    assert outcome.validation_outcome.decision is CandidateValidationDecision.REJECTED
    assert CandidateValidationReason.REQUEST_INVALID in outcome.validation_outcome.reasons
    assert outcome.validated_selection is None


def test_duplicate_claim_key_is_rejected_by_the_existing_validator_without_silent_dedup() -> None:
    guide_outcome = two_claim_outcome()
    first, second = card_of(guide_outcome).claims
    assert first.claim_key != second.claim_key
    rewritten = _with_card_claims(guide_outcome, (first, replace(second, claim_key=first.claim_key)))

    outcome = run(rewritten)

    # Both claims survive projection. Neither is dropped, merged or renamed.
    assert outcome.candidate_set is not None
    assert [claim.claim_key for claim in outcome.candidate_set.claims] == [first.claim_key, first.claim_key]
    assert len(outcome.candidate_set.citations) == 2
    assert outcome.support_receipts is not None
    assert len(outcome.support_receipts) == 2

    assert outcome.decision is GuideClaimCitationDecision.STOPPED
    assert outcome.stopped_stage is GuideClaimCitationStage.CLAIM_CITATION_VALIDATION
    assert outcome.validation_outcome is not None
    assert outcome.validation_outcome.execution_status is CandidateValidationExecutionStatus.VALIDATION_ERROR
    assert outcome.validation_outcome.decision is CandidateValidationDecision.REJECTED
    assert CandidateValidationReason.CLAIM_IDENTITY_INVALID in outcome.validation_outcome.reasons
    assert outcome.validated_selection is None


# ==============================================================================
# G. Source-level guards
# ==============================================================================

_MODULE_PATH = pathlib.Path(guide_claim_citation_validation.__file__)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "sqlalchemy",
    "backend",
    "ai_worker.tasks.rag.citation_authorization",
    "ai_worker.tasks.rag.citation_finalizer",
    "ai_worker.tasks.rag.evidence_gate",
    "ai_worker.tasks.rag.production_evidence_gate",
    "httpx",
    "requests",
    "openai",
)


def _imported_module_names() -> set[str]:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if type(node) is ast.Import:
            names.update(alias.name for alias in node.names)
        elif type(node) is ast.ImportFrom and node.module is not None:
            names.add(node.module)
    return names


def test_module_imports_no_persistence_network_or_out_of_scope_domain() -> None:
    imported = _imported_module_names()

    assert not [
        name
        for name in imported
        if any(name == fragment or name.startswith(f"{fragment}.") for fragment in _FORBIDDEN_IMPORT_FRAGMENTS)
    ]


def test_module_references_no_clock_and_no_legacy_evidence_gate_symbol() -> None:
    """AST-based, so this guard never trips over its own docstring prose."""
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    attribute_names = {node.attr for node in ast.walk(tree) if type(node) is ast.Attribute}
    identifiers = {node.id for node in ast.walk(tree) if type(node) is ast.Name}
    referenced = attribute_names | identifiers

    assert "now" not in referenced
    assert "utcnow" not in referenced
    assert "EvidenceGateOutcome" not in referenced
    assert "GatePassedKnowledgeEvidenceSelection" not in referenced
    assert "build_citation_authorization_request" not in referenced
    assert "CitationAuthorizationReceipt" not in referenced
    assert "AuthorizedCitationSelection" not in referenced
