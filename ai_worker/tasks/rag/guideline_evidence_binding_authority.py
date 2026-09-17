"""#781 Dynamic Guideline Evidence Binding Authority — request-scoped approval seam.

Closes the gap between the #774 production evidence input contract and the
`guideline_card` finalizer: given one request's `ProductionGuidelineEvidenceSet`,
its pinned `MedicationIdentityRef` tuple and the `GuidelineCardDraft` the Generator
produced, this module derives the `ApprovedGuidelineEvidenceBinding` tuple the
finalizer requires and supplies a `GuidelineApprovalVerifierPort` implementation for
that same request.

Scope & Authority Boundaries:
- #774 production evidence is the only evidence input. The legacy RAG-14
  `evidence_gate` domain (`EvidenceGateOutcome`, `GatePassedKnowledgeEvidenceSelection`,
  `canonical_gate_selection_hash()`) is forbidden here and is neither imported nor
  reconstructed. `selection_projection_sha256` always means
  `compute_production_guideline_evidence_selection_hash()`.
- No authority re-judgment. Source approval, member eligibility, assessment validity
  and content freshness were decided by #760 and are not re-evaluated. Derivation
  checks only that each draft citation exact-matches a production selection it was
  handed.
- Derivation is draft-driven. Only the `(claim, citation)` combinations that actually
  appear in the draft become bindings. No Cartesian product over medications, evidence
  or scopes is ever formed.
- Binding artifact identity is fixed by this contract. The caller cannot choose
  `artifact_code` or `version`, and `ApprovedGuidelineEvidenceBinding.create()`'s own
  self-hash payload is used unchanged.
- The verifier recomputes. `RequestScopedGuidelineApprovalVerifier` derives its
  expected dynamic binding refs from the authoritative request inputs, never from an
  issuer-supplied binding tuple. Issuer and verifier share only the pure canonical
  derivation function below.
- Static approval membership is exactly the #729 READY pins
  (`ReadyGuideRuntimeContext.policy_ref` and `fallback_refs`). `approval_pack_ref` and
  `candidate_ref` are provenance, not approval targets, and never verify.
- Pure boundary. Synchronous, I/O-free and deterministic: no DB, SQLAlchemy,
  repository, migration, network, clock, `datetime.now()`, async or worker. The #746
  Assessment/Eligibility authority is not read a second time.
- Not implemented here: binding persistence, Generator invocation, #180 orchestration
  wiring, Guideline Card persistence, Citation Authorization, Release Gate, and
  `PUBLIC_TRACK_F`.

Root-of-trust limitation (UNRESOLVED):
`build_request_scoped_guideline_authority()` requires a `GuideRuntimePreflightOutcome`
that is READY with a `ready_context`, so the production path can only reach this seam
through #729. That is a *sequencing* boundary, not an unforgeable capability:
`GuideRuntimePreflightOutcome` and `ReadyGuideRuntimeContext` are ordinary frozen
dataclasses and Python value provenance cannot be authenticated inside a pure seam.
This module therefore does not claim that a READY outcome proves authenticity, and it
deliberately does not invent a signature, token or capability framework to do so. What
it does guarantee is that anything other than a well-formed READY outcome fails closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_evidence_handoff import canonical_jcs_sha256
from ai_worker.tasks.rag.guide_runtime_preflight import (
    GuideRuntimePreflightDecision,
    GuideRuntimePreflightOutcome,
    ReadyGuideRuntimeContext,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineEvidenceBinding,
    GuidelineActionClass,
    GuidelineApprovalVerificationFailure,
    GuidelineApprovalVerificationSuccess,
    GuidelineCardDraft,
    GuidelineCitationDraft,
    GuidelineClaimDraft,
    GuidelineScope,
    MedicationIdentityRef,
)
from ai_worker.tasks.rag.guideline_production_evidence import (
    PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION,
    ProductionGuidelineEvidence,
    ProductionGuidelineEvidenceSet,
    compute_production_guideline_evidence_selection_hash,
)

__all__ = [
    "GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_CODE",
    "GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_VERSION",
    "GUIDELINE_APPROVAL_VERIFIER_REF",
    "GUIDELINE_EVIDENCE_BINDING_ARTIFACT_CODE",
    "GUIDELINE_EVIDENCE_BINDING_ARTIFACT_VERSION",
    "GUIDELINE_EVIDENCE_BINDING_DERIVATION_VERSION",
    "GuidelineAuthorityFailureReason",
    "GuidelineEvidenceBindingDerivationOutcome",
    "RequestScopedGuidelineApprovalVerifier",
    "RequestScopedGuidelineAuthority",
    "RequestScopedGuidelineAuthorityOutcome",
    "build_request_scoped_guideline_authority",
    "compute_guideline_approval_verifier_artifact_ref",
    "derive_guideline_evidence_bindings",
]

# Production binding artifact identity. The repository had no approved production
# identity before #781: `guideline-evidence-binding` appeared only as a synthetic test
# fixture code, and synthetic fixture versions (`...@synthetic-1`) are not promoted.
# This contract fixes both values, and no caller parameter can change them.
GUIDELINE_EVIDENCE_BINDING_ARTIFACT_CODE = "guideline-evidence-binding"
GUIDELINE_EVIDENCE_BINDING_ARTIFACT_VERSION = "guideline-evidence-binding-v1"

# The derivation rule version. It is bound into the verifier identity so a future
# change to which draft/evidence facts a binding covers produces a different verifier.
GUIDELINE_EVIDENCE_BINDING_DERIVATION_VERSION = "guideline-evidence-binding-derivation-v1"

GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_CODE = "request-scoped-guideline-approval-verifier"
GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_VERSION = "request-scoped-guideline-approval-verifier-v1"


class GuidelineAuthorityFailureReason(StrEnum):
    """Why a request produced no bindings and no authority. One enum, fail closed."""

    PREFLIGHT_NOT_READY = "PREFLIGHT_NOT_READY"
    REQUEST_INVALID = "REQUEST_INVALID"
    DRAFT_INVALID = "DRAFT_INVALID"
    MEDICATION_NOT_PINNED = "MEDICATION_NOT_PINNED"
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    CITATION_MISMATCH = "CITATION_MISMATCH"
    DUPLICATE_BINDING = "DUPLICATE_BINDING"


@dataclass(frozen=True, slots=True)
class GuidelineEvidenceBindingDerivationOutcome:
    """`bindings` is populated exactly when `reason` is None. There is no partial success."""

    bindings: tuple[ApprovedGuidelineEvidenceBinding, ...] | None
    reason: GuidelineAuthorityFailureReason | None


def compute_guideline_approval_verifier_artifact_ref() -> ImmutableArtifactRef:
    """Deterministic identity of the request-scoped Guideline approval verification rule.

    Follows the repository's existing verifier identity convention
    (`rag_runtime.evidence_authority.compute_verifier_artifact_ref`): a canonical
    contract projection digest, not a source-file byte hash and not a SHA-256 of one
    arbitrary constant string. The preimage binds the domain, artifact identity, the
    artifact classes this verifier may approve and must reject, the static pin source,
    the dynamic binding derivation rule version, the #774 production selection
    projection version, and the failure behavior.

    Nothing request-specific is included, so the same implementation and configuration
    yield the same ref for every request. The digest proves identity and integrity of
    the rule only; it is not itself an approval.
    """
    payload = {
        "approvable_artifact_classes": [
            "dynamic_guideline_evidence_binding",
            "ready_guide_runtime_fallback_pin",
            "ready_guide_runtime_policy_pin",
        ],
        "artifact_code": GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_CODE,
        "binding_artifact_code": GUIDELINE_EVIDENCE_BINDING_ARTIFACT_CODE,
        "binding_artifact_version": GUIDELINE_EVIDENCE_BINDING_ARTIFACT_VERSION,
        "binding_derivation_version": GUIDELINE_EVIDENCE_BINDING_DERIVATION_VERSION,
        "contract": "GuidelineApprovalVerifierPort",
        "domain": "rag15-guideline-approval",
        "dynamic_binding_source": (
            "independent recomputation from ProductionGuidelineEvidenceSet, pinned "
            "MedicationIdentityRef tuple and GuidelineCardDraft; never the issuer binding tuple"
        ),
        "failure_behavior": "fail_closed_on_any_ref_outside_the_recomputed_approval_set",
        "production_selection_projection_version": (PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION),
        "rejected_artifact_classes": [
            "rag15_approval_pack_ref",
            "rag15_guideline_candidate_ref",
        ],
        "semantics": (
            "Request-scoped bridge that verifies the #729 READY static policy and fallback "
            "pins and the #781 dynamic Guideline evidence binding refs recomputed from the "
            "authoritative request inputs. It issues no new static approval."
        ),
        "static_pin_source": "ReadyGuideRuntimeContext.policy_ref and ReadyGuideRuntimeContext.fallback_refs",
        "version": GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_VERSION,
    }
    return ImmutableArtifactRef(
        GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_CODE,
        GUIDELINE_APPROVAL_VERIFIER_ARTIFACT_VERSION,
        canonical_jcs_sha256(payload),
    )


GUIDELINE_APPROVAL_VERIFIER_REF = compute_guideline_approval_verifier_artifact_ref()


def _failed(reason: GuidelineAuthorityFailureReason) -> GuidelineEvidenceBindingDerivationOutcome:
    return GuidelineEvidenceBindingDerivationOutcome(bindings=None, reason=reason)


def _evidence_by_key(
    evidence: object,
) -> dict[str, ProductionGuidelineEvidence] | None:
    """Index the production selections by `evidence_key`, rejecting a malformed set.

    Only the structural shape this seam consumes is checked. The full production
    evidence validation stays with the finalizer, which runs it again.
    """
    if type(evidence) is not ProductionGuidelineEvidenceSet:
        return None
    if type(evidence.selections) is not tuple or not evidence.selections:
        return None
    indexed: dict[str, ProductionGuidelineEvidence] = {}
    for item in evidence.selections:
        if type(item) is not ProductionGuidelineEvidence or item.evidence_key in indexed:
            return None
        indexed[item.evidence_key] = item
    return indexed


def _pinned_medications(value: object) -> frozenset[MedicationIdentityRef] | None:
    """Freeze the caller's pinned medication identities for exact membership tests.

    No identity is created, normalized or canonical-code corrected here. A duplicate
    pin is a malformed input rather than something to collapse silently.
    """
    if type(value) is not tuple or not value:
        return None
    pinned: set[MedicationIdentityRef] = set()
    for item in value:
        if type(item) is not MedicationIdentityRef or item in pinned:
            return None
        pinned.add(item)
    return frozenset(pinned)


def _is_derivable_draft(draft: object) -> bool:
    if type(draft) is not GuidelineCardDraft or type(draft.claims) is not tuple or not draft.claims:
        return False
    for claim in draft.claims:
        if (
            type(claim) is not GuidelineClaimDraft
            or type(claim.medication_identity) is not MedicationIdentityRef
            or type(claim.scope) is not GuidelineScope
            or type(claim.action_class) is not GuidelineActionClass
            or type(claim.action_text) is not SensitiveText
            or type(claim.citations) is not tuple
            or not claim.citations
            or any(type(citation) is not GuidelineCitationDraft for citation in claim.citations)
        ):
            return False
    return True


def _citation_matches_evidence(
    citation: GuidelineCitationDraft,
    evidence: ProductionGuidelineEvidence,
) -> bool:
    """Exact-match every production Source coordinate the citation claims.

    `evidence_key` alone is not a binding key: the same key must also agree on snapshot,
    member, code, version, locator and content digest before a binding is issued. This
    is a coordinate identity check, not a re-evaluation of the approval, freshness or
    assessment validity that #760 already decided.
    """
    return (
        citation.source_snapshot_id == evidence.source_snapshot_id
        and citation.source_snapshot_member_id == evidence.source_snapshot_member_id
        and citation.source_code == evidence.source_code
        and citation.source_version == evidence.source_version
        and citation.locator == evidence.locator
        and citation.content_sha256 == evidence.content_sha256
    )


def derive_guideline_evidence_bindings(
    *,
    evidence: ProductionGuidelineEvidenceSet,
    medication_identities: tuple[MedicationIdentityRef, ...],
    draft: GuidelineCardDraft,
) -> GuidelineEvidenceBindingDerivationOutcome:
    """Derive the request's bindings from the draft's own claim/citation combinations.

    This is the single canonical derivation rule. The issuer factory and the
    request-scoped verifier both call it, and the verifier calls it on the
    authoritative inputs rather than reusing the issuer's result.

    Deterministic: bindings come out in draft claim order, then citation order, and the
    binding identity `(evidence_key, medication_identity, scope)` must be unique across
    the whole draft. A repeat is a malformed draft and fails the entire derivation; it
    is never deduplicated, repaired or normalized.
    """
    indexed = _evidence_by_key(evidence)
    pinned = _pinned_medications(medication_identities)
    if indexed is None or pinned is None:
        return _failed(GuidelineAuthorityFailureReason.REQUEST_INVALID)
    if not _is_derivable_draft(draft):
        return _failed(GuidelineAuthorityFailureReason.DRAFT_INVALID)

    bindings: list[ApprovedGuidelineEvidenceBinding] = []
    seen: set[tuple[str, MedicationIdentityRef, GuidelineScope]] = set()
    for claim in draft.claims:
        if claim.medication_identity not in pinned:
            return _failed(GuidelineAuthorityFailureReason.MEDICATION_NOT_PINNED)
        action_text_sha256 = hashlib.sha256(claim.action_text.reveal().encode("utf-8")).hexdigest()
        for citation in claim.citations:
            selection = indexed.get(citation.evidence_key)
            if selection is None:
                return _failed(GuidelineAuthorityFailureReason.EVIDENCE_NOT_FOUND)
            if not _citation_matches_evidence(citation, selection):
                return _failed(GuidelineAuthorityFailureReason.CITATION_MISMATCH)
            identity = (citation.evidence_key, claim.medication_identity, claim.scope)
            if identity in seen:
                return _failed(GuidelineAuthorityFailureReason.DUPLICATE_BINDING)
            seen.add(identity)
            bindings.append(
                ApprovedGuidelineEvidenceBinding.create(
                    GUIDELINE_EVIDENCE_BINDING_ARTIFACT_CODE,
                    GUIDELINE_EVIDENCE_BINDING_ARTIFACT_VERSION,
                    medication_identity=claim.medication_identity,
                    scope=claim.scope,
                    action_class=claim.action_class,
                    evidence_key=citation.evidence_key,
                    assessment_artifact_ref=selection.assessment_artifact_ref,
                    selection_projection_sha256=compute_production_guideline_evidence_selection_hash(selection),
                    action_text_sha256=action_text_sha256,
                )
            )
    return GuidelineEvidenceBindingDerivationOutcome(bindings=tuple(bindings), reason=None)


class RequestScopedGuidelineApprovalVerifier:
    """`GuidelineApprovalVerifierPort` implementation scoped to one Guide request.

    It issues no approval of its own. Its approval set is built once, from the #729
    READY static pins plus the dynamic binding refs it recomputes from the
    authoritative request inputs. A ref outside that set — an unknown artifact, an
    `approval_pack_ref`, a `candidate_ref`, a tampered binding ref, or a binding that
    exists only in an issuer's result tuple — fails closed.
    """

    _approved_refs: frozenset[ImmutableArtifactRef]

    __slots__ = ("_approved_refs",)

    def __init__(
        self,
        *,
        ready_context: ReadyGuideRuntimeContext,
        evidence: ProductionGuidelineEvidenceSet,
        medication_identities: tuple[MedicationIdentityRef, ...],
        draft: GuidelineCardDraft,
    ) -> None:
        approved: set[ImmutableArtifactRef] = set()
        if type(ready_context) is ReadyGuideRuntimeContext:
            # Only the policy and fallback pins #729 already verified. `approval_pack_ref`
            # and `candidate_ref` are deliberately absent: they are provenance context,
            # not artifacts a Guideline Card asks this port to approve.
            if type(ready_context.policy_ref) is ImmutableArtifactRef:
                approved.add(ready_context.policy_ref)
            if type(ready_context.fallback_refs) is tuple:
                approved.update(ref for ref in ready_context.fallback_refs if type(ref) is ImmutableArtifactRef)
        outcome = derive_guideline_evidence_bindings(
            evidence=evidence,
            medication_identities=medication_identities,
            draft=draft,
        )
        if outcome.bindings is not None:
            approved.update(binding.artifact_ref for binding in outcome.bindings)
        self._approved_refs = frozenset(approved)

    def verify(
        self,
        artifact_ref: ImmutableArtifactRef,
    ) -> GuidelineApprovalVerificationSuccess | GuidelineApprovalVerificationFailure:
        if type(artifact_ref) is not ImmutableArtifactRef or artifact_ref not in self._approved_refs:
            return GuidelineApprovalVerificationFailure()
        return GuidelineApprovalVerificationSuccess(
            artifact_ref=ImmutableArtifactRef(
                artifact_ref.artifact_code,
                artifact_ref.version,
                artifact_ref.content_sha256,
            ),
            verifier_artifact_ref=GUIDELINE_APPROVAL_VERIFIER_REF,
        )


@dataclass(frozen=True, slots=True)
class RequestScopedGuidelineAuthority:
    """The two things a `finalize_guideline_card()` call needs for this request.

    `approval_verifier` does not read `bindings`. Adding a forged binding to this tuple
    therefore does not make it verifiable.
    """

    bindings: tuple[ApprovedGuidelineEvidenceBinding, ...]
    approval_verifier: RequestScopedGuidelineApprovalVerifier


@dataclass(frozen=True, slots=True)
class RequestScopedGuidelineAuthorityOutcome:
    """`authority` is populated exactly when `reason` is None."""

    authority: RequestScopedGuidelineAuthority | None
    reason: GuidelineAuthorityFailureReason | None


def _ready_context_of(preflight_outcome: object) -> ReadyGuideRuntimeContext | None:
    """The #729 sequencing boundary this seam requires, not an authenticity proof.

    A caller can still construct a READY-shaped outcome in Python; see the module
    docstring's UNRESOLVED note. What this does enforce is that every other shape —
    a bare `ReadyGuideRuntimeContext`, a BLOCKED outcome, a READY outcome with no
    context, or a READY outcome still carrying a blocking reason — fails closed.
    """
    if type(preflight_outcome) is not GuideRuntimePreflightOutcome:
        return None
    if preflight_outcome.decision is not GuideRuntimePreflightDecision.READY:
        return None
    if preflight_outcome.reason is not None:
        return None
    if type(preflight_outcome.ready_context) is not ReadyGuideRuntimeContext:
        return None
    return preflight_outcome.ready_context


def build_request_scoped_guideline_authority(
    preflight_outcome: GuideRuntimePreflightOutcome,
    *,
    evidence: ProductionGuidelineEvidenceSet,
    medication_identities: tuple[MedicationIdentityRef, ...],
    draft: GuidelineCardDraft,
) -> RequestScopedGuidelineAuthorityOutcome:
    """Build one request's binding tuple and approval verifier, or fail closed.

    Pure and synchronous. This does not wire #180 orchestration, invoke the Generator,
    persist anything, or read the #746 Assessment/Eligibility authority again.
    """
    ready_context = _ready_context_of(preflight_outcome)
    if ready_context is None:
        return RequestScopedGuidelineAuthorityOutcome(
            authority=None,
            reason=GuidelineAuthorityFailureReason.PREFLIGHT_NOT_READY,
        )

    derivation = derive_guideline_evidence_bindings(
        evidence=evidence,
        medication_identities=medication_identities,
        draft=draft,
    )
    if derivation.bindings is None:
        return RequestScopedGuidelineAuthorityOutcome(authority=None, reason=derivation.reason)

    return RequestScopedGuidelineAuthorityOutcome(
        authority=RequestScopedGuidelineAuthority(
            bindings=derivation.bindings,
            approval_verifier=RequestScopedGuidelineApprovalVerifier(
                ready_context=ready_context,
                evidence=evidence,
                medication_identities=medication_identities,
                draft=draft,
            ),
        ),
        reason=None,
    )
