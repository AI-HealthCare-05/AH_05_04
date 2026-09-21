"""#180 Slice 3 — Guideline Card -> Claim/Citation validation handoff (#794).

Slice 2 (`guide_generation_card_orchestration.orchestrate_guide_generation_card`)
stops at a `GuidelineCardOutcome`. This module consumes that result and carries a
GENERATED Card the rest of the way to a `ValidatedCitationSelection`:

    #787 GuideGenerationCardOutcome (COMPLETED + GENERATED Card)
      -> Card citation <-> #760 VerifiedGuideEvidenceHandoff exact match
      -> ClaimCitationCandidateSet
      -> claim-level Guideline support assessment artifacts
      -> ClaimSupportVerificationReceipt[]  (exactly one per claim)
      -> validate_claim_citations()
      -> ValidatedCitationSelection

Scope & Authority Boundaries:
- GENERATED only. A fallback answer is never dressed up as a medical claim. A
  STOPPED run, a missing `card_outcome`, and the NO_RESULT / LIMITED / STALE /
  VALIDATION_REJECTED Card statuses all stop at `CARD_NOT_ELIGIBLE` without
  producing a single candidate.
- Single canonical input. The request carries the #787 outcome and nothing else. A
  caller cannot hand in its own `GuidelineCard`, `VerifiedGuideEvidenceHandoff`,
  generation provenance, validator policy or support assessment ref, because a
  second input channel would let it pair a Card with a handoff that never produced
  it.
- Upstream authority is reused, not re-judged. Source approval, member eligibility,
  assessment validity and content freshness were decided by #760 and are not
  re-evaluated. `upstream_outcome.ready_inputs.evidence_handoff` is the only source
  of `SourceExecutionProvenance`, because the #774 production evidence projection
  deliberately drops those audit fields and the Card therefore cannot carry them.
  No Decision ref is ever created here.
- Exact match or fail closed. Every Card citation must find one
  `VerifiedGuideEvidenceSelection` with the same `evidence_key` that also agrees on
  snapshot, member, source code, source version, locator, content digest, assessment
  artifact, eligibility receipt, retrieval receipt and verifier artifact. Nothing is
  normalized, corrected, deduplicated or dropped on the way.
- Claim-level support authority. A claim may cite several pieces of evidence, so no
  single citation's `assessment_artifact_ref` may stand in for the claim. Each claim
  gets its own deterministic support assessment artifact whose preimage binds every
  one of that claim's citations; see
  `compute_guideline_claim_support_assessment_ref()`.
- Request-independent verifier identity. `GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF` is a
  canonical contract projection following the repository's existing verifier identity
  convention (`guideline_evidence_binding_authority.compute_guideline_approval_verifier_artifact_ref`),
  not a SHA-256 of one arbitrary constant and not another domain's verifier reused
  because it sounds similar. The same is true of the validator policy identity.
- Existing kernel reused verbatim. `validate_claim_citations()` and
  `canonical_claim_support_projection_hash()` are called, never reimplemented, and
  their verdicts are preserved unchanged. No reason, status or fallback is remapped
  into a new enum.
- Legacy domain forbidden. RAG-14 `EvidenceGateOutcome` /
  `GatePassedKnowledgeEvidenceSelection` are neither imported nor reconstructed.
- Pure boundary. Synchronous, I/O-free and deterministic: no DB, SQLAlchemy,
  repository, migration, network, clock, `datetime.now()` or worker.

Terminal point: `ValidatedCitationSelection`. `CitationAuthorizationRequest`,
`CitationAuthorizationReceipt`, `build_citation_authorization_request()`, the
`citation_finalizer`, `AuthorizedCitationSelection`, the PATIENT_CITATION Guard,
`DiscardGeneratedContent` wiring, the Release Gate, persistence, the Guide API and
`PUBLIC_TRACK_F` are all out of scope. A VALIDATED outcome here does not mean a
citation was authorized, a release was approved, or a Card was persisted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.claim_citation_validator import (
    CLAIM_SUPPORT_PROJECTION_VERSION,
    VALIDATED_SELECTION_PROJECTION_VERSION,
    CandidateValidationDecision,
    CandidateValidationExecutionStatus,
    CitationCandidate,
    CitationSourceType,
    ClaimCandidate,
    ClaimCitationCandidateSet,
    ClaimCitationValidationOutcome,
    ClaimKind,
    ClaimSupportAssertion,
    ClaimSupportStatus,
    ClaimSupportVerificationReceipt,
    ClaimTargetRef,
    GenerationProvenance,
    LifestyleGuidelineEvidenceRef,
    SourceExecutionProvenance,
    ValidatedCitationSelection,
    canonical_claim_support_projection_hash,
    validate_claim_citations,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_aggregate_evidence import GuideAggregateEvidence
from ai_worker.tasks.rag.guide_evidence_handoff import (
    VerifiedGuideEvidenceHandoff,
    VerifiedGuideEvidenceSelection,
    canonical_jcs_sha256,
)
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOutcome,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCard,
    GuidelineCardStatus,
    GuidelineCitation,
    GuidelineCitationSourceType,
    GuidelineClaim,
    MedicationIdentityRef,
)

__all__ = [
    "GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_CODE",
    "GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_VERSION",
    "GUIDELINE_CLAIM_SUPPORT_DERIVATION_VERSION",
    "GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_CODE",
    "GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_VERSION",
    "GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF",
    "GUIDE_CLAIM_CITATION_TARGET_KIND",
    "GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_CODE",
    "GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_VERSION",
    "GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_REF",
    "GuideClaimCitationDecision",
    "GuideClaimCitationStage",
    "GuideClaimCitationValidationOutcome",
    "GuideClaimCitationValidationRequest",
    "compute_guide_claim_citation_validator_policy_ref",
    "compute_guideline_claim_support_assessment_ref",
    "compute_guideline_claim_support_verifier_artifact_ref",
    "run_guide_claim_citation_validation",
]

# Claim-level support assessment artifact identity. The repository had no production
# claim support assessment identity before #794, and `claim-citation-validator`
# existed only as a synthetic test fixture code, which is not promoted. This contract
# fixes these values and no caller parameter can change them.
GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_CODE = "guideline-claim-support-assessment"
GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_VERSION = "guideline-claim-support-assessment-v1"

# The derivation rule version, bound into both the per-claim assessment preimage and
# the verifier identity, so a future change to which facts a claim's support covers
# produces both a different assessment ref and a different verifier ref.
GUIDELINE_CLAIM_SUPPORT_DERIVATION_VERSION = "guideline-claim-support-derivation-v1"

GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_CODE = "guideline-claim-support-verifier"
GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_VERSION = "guideline-claim-support-verifier-v1"

GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_CODE = "guide-claim-citation-validator-policy"
GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_VERSION = "guide-claim-citation-validator-policy-v1"

# `ClaimTargetRef.target_kind` for a Guideline Card. The target ref itself is the
# Card's own self-hash, so the candidate set names exactly the Card it came from.
GUIDE_CLAIM_CITATION_TARGET_KIND = "GUIDELINE_CARD"


class GuideClaimCitationDecision(StrEnum):
    """#794-local state. Not a public Guide runtime, release or AI Job state.

    VALIDATED means the existing Claim/Citation kernel returned EVALUATED/VALIDATED
    with a `ValidatedCitationSelection`. It does not mean the citations were
    authorized or that anything may be released.
    """

    VALIDATED = "VALIDATED"
    STOPPED = "STOPPED"


class GuideClaimCitationStage(StrEnum):
    """The stage a stopped run did not get past.

    There is deliberately no CLAIM_SUPPORT_AUTHORITY member: once projection
    succeeds, the claim-level support assessment and the receipt tuple are total
    deterministic functions of the candidate set and cannot fail on their own.
    """

    CARD_NOT_ELIGIBLE = "CARD_NOT_ELIGIBLE"
    CARD_PROJECTION = "CARD_PROJECTION"
    CLAIM_CITATION_VALIDATION = "CLAIM_CITATION_VALIDATION"


@dataclass(frozen=True, slots=True)
class GuideClaimCitationValidationRequest:
    """One Guide request's #787 result, and nothing else.

    The Card, the authoritative evidence handoff and the generation provenance are
    deliberately absent as separate inputs: re-accepting any of them would let a
    caller pair a Card with a handoff or a provenance that never produced it. Their
    canonical sources are `guide_outcome.card_outcome.card`,
    `guide_outcome.upstream_outcome.ready_inputs.evidence_handoff` and
    `card.provenance`.
    """

    guide_outcome: GuideGenerationCardOutcome


@dataclass(frozen=True, slots=True)
class GuideClaimCitationValidationOutcome:
    """Every stage's own result, unchanged.

    A `None` field means that stage was never reached, so a caller can tell an
    ineligible Card from a projection mismatch from a validator rejection.
    `validation_outcome` carries the existing kernel's verdict verbatim, including
    its reasons, on both the VALIDATED and the rejected path.
    """

    decision: GuideClaimCitationDecision
    stopped_stage: GuideClaimCitationStage | None
    guide_outcome: GuideGenerationCardOutcome
    candidate_set: ClaimCitationCandidateSet | None
    support_receipts: tuple[ClaimSupportVerificationReceipt, ...] | None
    validation_outcome: ClaimCitationValidationOutcome | None
    validated_selection: ValidatedCitationSelection | None


def _artifact_payload(ref: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": ref.artifact_code,
        "content_sha256": ref.content_sha256,
        "version": ref.version,
    }


def compute_guideline_claim_support_verifier_artifact_ref() -> ImmutableArtifactRef:
    """Deterministic identity of the Guideline claim support derivation rule.

    Follows the repository's existing verifier identity convention
    (`guideline_evidence_binding_authority.compute_guideline_approval_verifier_artifact_ref`,
    itself following `rag_runtime.evidence_authority.compute_verifier_artifact_ref`):
    a canonical contract projection digest, not a source-file byte hash and not a
    SHA-256 of one arbitrary constant string. The preimage binds the domain, the
    artifact identity, the derivation rule version, the claim kind and support status
    this rule may assert, the authority inputs it derives support from, the assessment
    artifact identity it produces, the pure kernel projection versions it is bound to,
    and the failure behavior.

    Nothing request-specific is included, so the same implementation and configuration
    yield the same ref for every request. The digest proves identity and integrity of
    the rule only; it is not itself an approval, and it is not evidence that a
    semantic entailment check was performed — none is, and none is claimed.
    """
    payload = {
        "artifact_code": GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_CODE,
        "assessment_artifact_code": GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_CODE,
        "assessment_artifact_version": GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_VERSION,
        "asserted_claim_kind": ClaimKind.MEDICAL.value,
        "asserted_support_status": ClaimSupportStatus.SUPPORTED.value,
        "claim_support_projection_version": CLAIM_SUPPORT_PROJECTION_VERSION,
        "contract": "ClaimSupportVerificationReceipt",
        "derivation_version": GUIDELINE_CLAIM_SUPPORT_DERIVATION_VERSION,
        "domain": "rag16-guideline-claim-support",
        "failure_behavior": "fail_closed_on_any_card_citation_without_an_exact_upstream_handoff_match",
        "input_authority": [
            "GuidelineCard claims and citations",
            "GuidelineCitation.guideline_evidence_binding_ref and its binding verifier ref",
            "VerifiedGuideEvidenceHandoff selections from the #760 authoritative handoff",
        ],
        "semantics": (
            "Derives one claim-level support assessment per Guideline Card claim from every "
            "citation that claim carries, and issues exactly one support receipt per claim. "
            "It performs no NLI, entailment or semantic assessment of its own and issues no "
            "new Source, member, assessment or release approval."
        ),
        "validated_selection_projection_version": VALIDATED_SELECTION_PROJECTION_VERSION,
        "version": GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_VERSION,
    }
    return ImmutableArtifactRef(
        GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_CODE,
        GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_VERSION,
        canonical_jcs_sha256(payload),
    )


GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF = compute_guideline_claim_support_verifier_artifact_ref()


def compute_guide_claim_citation_validator_policy_ref() -> ImmutableArtifactRef:
    """Deterministic identity of the Claim/Citation validation policy this slice applies.

    The repository had no production `validator_policy_ref` identity before #794:
    `claim-citation-validator` appeared only as a synthetic test fixture code, and
    synthetic fixture identities are not promoted to production. This contract fixes
    the identity, and it is not a caller parameter.

    The digest is a canonical projection of the validation contract actually applied —
    the pure kernel's own projection versions, the Card-derived candidate semantics and
    the support receipt issuer — so changing any of them changes the policy ref. It is
    not `sha256(b"claim-citation-validator-v1")`.
    """
    payload = {
        "artifact_code": GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_CODE,
        "candidate_citation_source_type": CitationSourceType.LIFESTYLE_GUIDELINE.value,
        "candidate_claim_kind": ClaimKind.MEDICAL.value,
        "candidate_source": (
            "GuidelineCard claims and citations projected against the #760 authoritative "
            "VerifiedGuideEvidenceHandoff; GENERATED Cards only"
        ),
        "claim_support_projection_version": CLAIM_SUPPORT_PROJECTION_VERSION,
        "contract": "ClaimCitationCandidateSet.validator_policy_ref",
        "domain": "rag16-guide-claim-citation-validation",
        "kernel": "ai_worker.tasks.rag.claim_citation_validator.validate_claim_citations",
        "semantics": (
            "Names the validation rule applied to one Guideline Card's candidate set. The "
            "kernel's own decision, execution status and reasons are preserved verbatim and "
            "are never remapped by this policy."
        ),
        "support_receipt_verifier_artifact_code": GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_CODE,
        "support_receipt_verifier_artifact_version": GUIDELINE_CLAIM_SUPPORT_VERIFIER_ARTIFACT_VERSION,
        "target_kind": GUIDE_CLAIM_CITATION_TARGET_KIND,
        "validated_selection_projection_version": VALIDATED_SELECTION_PROJECTION_VERSION,
        "version": GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_VERSION,
    }
    return ImmutableArtifactRef(
        GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_CODE,
        GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_ARTIFACT_VERSION,
        canonical_jcs_sha256(payload),
    )


GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_REF = compute_guide_claim_citation_validator_policy_ref()


def _medication_payload(value: MedicationIdentityRef) -> dict[str, str]:
    return {
        "canonical_code": value.canonical_code,
        "code_system": value.code_system,
        "prescription_version_medication_id": value.prescription_version_medication_id,
    }


def _claim_support_citation_entry(citation: GuidelineCitation) -> dict[str, object]:
    """The support facts one citation contributes to its claim's assessment.

    Every fact here is already verified upstream: the assessment artifact and the
    Source coordinates come from #760, the binding ref and its verifier ref from #781.
    This projection binds them to the claim; it does not re-decide any of them.
    """
    return {
        "assessment_artifact_ref": _artifact_payload(citation.assessment_artifact_ref),
        "content_sha256": citation.content_sha256,
        "evidence_key": citation.evidence_key,
        "guideline_evidence_binding_ref": _artifact_payload(citation.guideline_evidence_binding_ref),
        "guideline_evidence_binding_verifier_ref": _artifact_payload(citation.guideline_evidence_binding_verifier_ref),
        "locator": citation.locator,
        "source_version": citation.source_version,
    }


def compute_guideline_claim_support_assessment_ref(
    card: GuidelineCard,
    claim: GuidelineClaim,
) -> ImmutableArtifactRef:
    """One claim's support assessment artifact, bound to *every* citation it carries.

    A Guideline claim may cite several pieces of evidence. Reusing the first, the last
    or any other single citation's `assessment_artifact_ref` as the claim's support
    would silently drop the rest, so the claim gets its own artifact whose preimage
    contains a canonically sorted entry for each of its citations. Changing the action
    text, or any one citation's evidence key, assessment ref, binding ref, binding
    verifier ref, source version, locator or content digest, changes this ref.

    The citation entries are sorted by their own canonical serialization so that a
    Card whose citations arrive in a different order but whose citation set is
    identical yields the same assessment ref. That canonicalization is confined to
    this preimage; the candidate `display_order` contract keeps the Card's own order
    and is never silently reordered.

    Artifact code and version are fixed by this contract; the caller chooses nothing.
    """
    entries = sorted(
        (_claim_support_citation_entry(citation) for citation in claim.citations),
        key=lambda entry: canonical_jcs_sha256(entry),
    )
    payload = {
        "action_class": claim.action_class.value,
        "action_text_sha256": _text_digest(claim.action_text.reveal()),
        "card_artifact_ref": _artifact_payload(card.artifact_ref),
        "citations": entries,
        "claim_key": claim.claim_key,
        "medication_identity": _medication_payload(claim.medication_identity),
        "projection_version": GUIDELINE_CLAIM_SUPPORT_DERIVATION_VERSION,
        "scope": claim.scope.value,
    }
    return ImmutableArtifactRef(
        GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_CODE,
        GUIDELINE_CLAIM_SUPPORT_ASSESSMENT_ARTIFACT_VERSION,
        canonical_jcs_sha256(payload),
    )


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generated_card(outcome: GuideGenerationCardOutcome) -> GuidelineCard | None:
    """The Card this slice may project, or None for every other #787 result.

    A fallback answer has no medical claim to validate, so NO_RESULT, LIMITED, STALE
    and VALIDATION_REJECTED are as ineligible as a STOPPED orchestration run. Nothing
    is downgraded, retried or converted.
    """
    if outcome.decision is not GuideGenerationCardDecision.COMPLETED:
        return None
    card_outcome = outcome.card_outcome
    if card_outcome is None or card_outcome.status is not GuidelineCardStatus.GENERATED:
        return None
    card = card_outcome.card
    if type(card) is not GuidelineCard or type(card.claims) is not tuple or not card.claims:
        return None
    return card


def _handoffs_of(outcome: GuideGenerationCardOutcome) -> tuple[VerifiedGuideEvidenceHandoff, ...] | None:
    """The #760 authoritative handoff the Card was ultimately generated from.

    This is the only place `SourceExecutionProvenance` can come from: the #774
    production evidence projection deliberately drops `member_kind`, the endpoint /
    operation / artifact member coordinates and both request Decision refs as
    audit-only, so the Card cannot carry them and this module must not forge them.
    """
    aggregate = outcome.aggregate
    if aggregate is not None:
        if type(aggregate) is not GuideAggregateEvidence or not aggregate.entries:
            return None
        handoffs = tuple(entry.handoff for entry in aggregate.entries)
        if not all(
            type(handoff) is VerifiedGuideEvidenceHandoff and type(handoff.selections) is tuple for handoff in handoffs
        ):
            return None
        return handoffs
    upstream_outcome = outcome.upstream_outcome
    if upstream_outcome is None or upstream_outcome.ready_inputs is None:
        return None
    handoff = upstream_outcome.ready_inputs.evidence_handoff
    if type(handoff) is not VerifiedGuideEvidenceHandoff or type(handoff.selections) is not tuple:
        return None
    return (handoff,)


def _selections_by_anchor(
    handoffs: tuple[VerifiedGuideEvidenceHandoff, ...],
) -> dict[tuple[object, str], tuple[VerifiedGuideEvidenceSelection, ...]] | None:
    indexed: dict[tuple[object, str], list[VerifiedGuideEvidenceSelection]] = {}
    for handoff in handoffs:
        seen_anchors: set[tuple[object, str]] = set()
        for selection in handoff.selections:
            if type(selection) is not VerifiedGuideEvidenceSelection:
                return None
            anchor = (selection.source_snapshot_id, selection.evidence_key)
            if anchor in seen_anchors:
                return None
            seen_anchors.add(anchor)
            indexed.setdefault(anchor, []).append(selection)
    return {anchor: tuple(selections) for anchor, selections in indexed.items()} or None


def _citation_matches_selection(
    citation: GuidelineCitation,
    selection: VerifiedGuideEvidenceSelection,
) -> bool:
    """Exact-match every fact the Card citation claims about upstream evidence.

    `evidence_key` alone is not a binding key. The Source coordinate and all four
    upstream authority references must agree as well, and a single disagreement fails
    the whole run. Nothing is normalized or corrected on the way through. This is an
    identity check, not a re-evaluation of the approval, freshness or assessment
    validity that #760 already decided.
    """
    return (
        citation.evidence_key == selection.evidence_key
        and citation.source_snapshot_id == selection.source_snapshot_id
        and citation.source_snapshot_member_id == selection.source_snapshot_member_id
        and citation.source_code == selection.source_code
        and citation.source_version == selection.source_version
        and citation.locator == selection.locator
        and citation.content_sha256 == selection.content_sha256
        and citation.assessment_artifact_ref == selection.assessment_artifact_ref
        and citation.eligibility_receipt_ref == selection.eligibility_receipt_ref
        and citation.retrieval_receipt_ref == selection.retrieval_receipt_ref
        and citation.verifier_artifact_ref == selection.verifier_artifact_ref
    )


def _execution_provenance(selection: VerifiedGuideEvidenceSelection) -> SourceExecutionProvenance:
    """Project, never synthesize. Both Decision refs are carried over as they stand."""
    return SourceExecutionProvenance(
        source_code=selection.source_code,
        source_version=selection.source_version,
        member_kind=selection.member_kind,
        endpoint_code=selection.endpoint_code,
        operation_code=selection.operation_code,
        artifact_code=selection.artifact_code,
        artifact_version=selection.artifact_version,
        request_source_decision_ref=selection.request_source_decision_ref,
        request_member_decision_ref=selection.request_member_decision_ref,
    )


def _evidence_ref(
    citation: GuidelineCitation,
    selection: VerifiedGuideEvidenceSelection,
) -> LifestyleGuidelineEvidenceRef:
    """Build the RAG-16 evidence reference from approved identities only.

    `guideline_artifact_ref` is the #781 Guideline Evidence Binding artifact the Card
    already carries. No Source Snapshot artifact ref is synthesized: production
    evidence has none, and inventing one would put a forged artifact identity into a
    citation. `guideline_evidence_ref` is the mechanical stable string form of the
    evidence identity the Card and the handoff agreed on.
    """
    return LifestyleGuidelineEvidenceRef(
        guideline_evidence_ref=(
            f"{citation.source_snapshot_id}/{citation.source_snapshot_member_id}/{citation.evidence_key}"
        ),
        guideline_artifact_ref=citation.guideline_evidence_binding_ref,
        source_version=citation.source_version,
        locator=citation.locator,
        content_sha256=citation.content_sha256,
        execution_provenance=_execution_provenance(selection),
    )


def _project_candidate_set(
    card: GuidelineCard,
    selections_by_anchor: dict[tuple[object, str], tuple[VerifiedGuideEvidenceSelection, ...]],
) -> ClaimCitationCandidateSet | None:
    """Project one GENERATED Card into a candidate set, or fail closed.

    `display_order` follows the Card: claims in Card order, citations in flattened
    Card order across all claims. Neither is reordered to match the support
    assessment's internal canonicalization.
    """
    claims: list[ClaimCandidate] = []
    citations: list[CitationCandidate] = []
    for claim_index, claim in enumerate(card.claims, start=1):
        if type(claim) is not GuidelineClaim or type(claim.citations) is not tuple or not claim.citations:
            return None
        seen_anchors: set[tuple[object, str]] = set()
        for citation in claim.citations:
            if (
                type(citation) is not GuidelineCitation
                or citation.source_type is not GuidelineCitationSourceType.LIFESTYLE_GUIDELINE
                or (citation.source_snapshot_id, citation.evidence_key) in seen_anchors
            ):
                return None
            anchor = (citation.source_snapshot_id, citation.evidence_key)
            seen_anchors.add(anchor)
            matching = tuple(
                selection
                for selection in selections_by_anchor.get(anchor, ())
                if _citation_matches_selection(citation, selection)
            )
            if len(matching) != 1:
                return None
            selection = matching[0]
            citations.append(
                CitationCandidate(
                    citation_key=f"{claim.claim_key}:{citation.evidence_key}",
                    claim_key=claim.claim_key,
                    source_type=CitationSourceType.LIFESTYLE_GUIDELINE,
                    evidence_ref=_evidence_ref(citation, selection),
                    display_order=len(citations) + 1,
                )
            )
        claims.append(
            ClaimCandidate(
                claim_key=claim.claim_key,
                claim_kind=ClaimKind.MEDICAL,
                text_digest=_text_digest(claim.action_text.reveal()),
                display_order=claim_index,
                support_assertion=ClaimSupportAssertion(
                    support_status=ClaimSupportStatus.SUPPORTED,
                    assessment_ref=compute_guideline_claim_support_assessment_ref(card, claim),
                ),
            )
        )
    return ClaimCitationCandidateSet(
        target=ClaimTargetRef(
            target_kind=GUIDE_CLAIM_CITATION_TARGET_KIND,
            target_ref=card.artifact_ref.content_sha256,
        ),
        claims=tuple(claims),
        citations=tuple(citations),
        generation_provenance=GenerationProvenance(
            prompt_ref=card.provenance.prompt_ref,
            model_ref=card.provenance.model_ref,
            parser_ref=card.provenance.parser_ref,
        ),
        validator_policy_ref=GUIDE_CLAIM_CITATION_VALIDATOR_POLICY_REF,
    )


def _support_receipts(
    candidate_set: ClaimCitationCandidateSet,
) -> tuple[ClaimSupportVerificationReceipt, ...]:
    """Exactly one receipt per claim, regardless of how many citations it carries.

    The receipt count tracks claims, never citations: one claim with two citations
    still yields one receipt. `projection_sha256` is the existing kernel's own
    `canonical_claim_support_projection_hash()` over the whole candidate set, so the
    receipt commits to every citation of that claim.
    """
    return tuple(
        ClaimSupportVerificationReceipt(
            claim_key=claim.claim_key,
            support_status=claim.support_assertion.support_status,
            claim_text_digest=claim.text_digest,
            assessment_ref=claim.support_assertion.assessment_ref,
            verifier_artifact_ref=GUIDELINE_CLAIM_SUPPORT_VERIFIER_REF,
            projection_sha256=canonical_claim_support_projection_hash(candidate_set, claim.claim_key),
        )
        for claim in candidate_set.claims
    )


def _stopped(
    stage: GuideClaimCitationStage,
    *,
    guide_outcome: GuideGenerationCardOutcome,
    candidate_set: ClaimCitationCandidateSet | None = None,
    support_receipts: tuple[ClaimSupportVerificationReceipt, ...] | None = None,
    validation_outcome: ClaimCitationValidationOutcome | None = None,
) -> GuideClaimCitationValidationOutcome:
    return GuideClaimCitationValidationOutcome(
        decision=GuideClaimCitationDecision.STOPPED,
        stopped_stage=stage,
        guide_outcome=guide_outcome,
        candidate_set=candidate_set,
        support_receipts=support_receipts,
        validation_outcome=validation_outcome,
        validated_selection=None,
    )


def run_guide_claim_citation_validation(
    request: GuideClaimCitationValidationRequest,
) -> GuideClaimCitationValidationOutcome:
    """Project a GENERATED Guideline Card and run the existing Claim/Citation kernel.

    Pure and synchronous. Terminal point is `ValidatedCitationSelection`; Citation
    Authorization is not wired here.
    """
    if type(request) is not GuideClaimCitationValidationRequest or type(request.guide_outcome) is not (
        GuideGenerationCardOutcome
    ):
        # There is no outcome to preserve, so there is nothing honest to return.
        raise TypeError("request must be a GuideClaimCitationValidationRequest carrying a #787 outcome")
    guide_outcome = request.guide_outcome

    # Phase 1 — eligibility. A fallback answer never becomes a medical claim.
    card = _generated_card(guide_outcome)
    if card is None:
        return _stopped(GuideClaimCitationStage.CARD_NOT_ELIGIBLE, guide_outcome=guide_outcome)
    handoffs = _handoffs_of(guide_outcome)
    if handoffs is None:
        return _stopped(GuideClaimCitationStage.CARD_NOT_ELIGIBLE, guide_outcome=guide_outcome)

    # Phase 2 — Card <-> authoritative handoff projection, exact match or fail closed.
    selections_by_anchor = _selections_by_anchor(handoffs)
    if selections_by_anchor is None:
        return _stopped(GuideClaimCitationStage.CARD_PROJECTION, guide_outcome=guide_outcome)
    candidate_set = _project_candidate_set(card, selections_by_anchor)
    if candidate_set is None:
        return _stopped(GuideClaimCitationStage.CARD_PROJECTION, guide_outcome=guide_outcome)

    # Phase 3 — claim-level support authority, one receipt per claim.
    support_receipts = _support_receipts(candidate_set)

    # Phase 4 — the existing pure kernel, called once and never reimplemented.
    validation_outcome = validate_claim_citations(candidate_set, support_receipts)
    validated_selection = validation_outcome.validated_selection
    if (
        validation_outcome.execution_status is not CandidateValidationExecutionStatus.EVALUATED
        or validation_outcome.decision is not CandidateValidationDecision.VALIDATED
        or validated_selection is None
    ):
        return _stopped(
            GuideClaimCitationStage.CLAIM_CITATION_VALIDATION,
            guide_outcome=guide_outcome,
            candidate_set=candidate_set,
            support_receipts=support_receipts,
            validation_outcome=validation_outcome,
        )
    return GuideClaimCitationValidationOutcome(
        decision=GuideClaimCitationDecision.VALIDATED,
        stopped_stage=None,
        guide_outcome=guide_outcome,
        candidate_set=candidate_set,
        support_receipts=support_receipts,
        validation_outcome=validation_outcome,
        validated_selection=validated_selection,
    )
