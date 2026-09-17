"""#180 Guide Runtime Preflight — RAG-15 Approval Pack runtime consumer.

Proves, before any Guide generation call is permitted, that the formally approved
RAG-15 runtime candidate exactly matches the currently configured Generator
identity, Guideline Policy, and Fallback Set.

READY means only that the RAG-15 *static* runtime candidate is approved and
exact-bound to the current runtime configuration. It does not authenticate Guide
evidence and is not Card, Citation, Release, or Public readiness. Production
Evidence Handoff remains dependent on the Assessment / Eligibility Authority work.

This module holds no patient, request, or evidence payload and never executes the
Generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guideline_approval_pack import (
    Rag15ApprovalDecisionVerifierPort,
    Rag15ApprovalPack,
    verify_rag15_approval_pack,
)
from ai_worker.tasks.rag.guideline_card import (
    ApprovedGuidelineFallback,
    GuidelineFallbackCode,
    GuidelineGenerationProvenance,
    VersionedGuidelinePolicy,
)
from ai_worker.tasks.rag.guideline_generator import GuidelineGeneratorPort


class RuntimeGuidelineGeneratorPort(GuidelineGeneratorPort, Protocol):
    """A Guideline Generator that exposes the provenance it will actually execute with."""

    @property
    def provenance(self) -> GuidelineGenerationProvenance: ...


class GuideRuntimePreflightDecision(StrEnum):
    """#180-local preflight decision. Not a public API, AI Job, or approval state."""

    READY = "READY"
    BLOCKED = "BLOCKED"


class GuideRuntimePreflightReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    APPROVAL_PACK_NOT_CONSUMABLE = "APPROVAL_PACK_NOT_CONSUMABLE"
    GENERATOR_PROVENANCE_MISMATCH = "GENERATOR_PROVENANCE_MISMATCH"
    POLICY_REF_MISMATCH = "POLICY_REF_MISMATCH"
    FALLBACK_SET_MISMATCH = "FALLBACK_SET_MISMATCH"


@dataclass(frozen=True, slots=True)
class GuideRuntimePreflightRequest:
    approval_pack: Rag15ApprovalPack
    policy: VersionedGuidelinePolicy
    fallbacks: tuple[ApprovedGuidelineFallback, ...]


@dataclass(frozen=True, slots=True)
class ReadyGuideRuntimeContext:
    approval_pack_ref: ImmutableArtifactRef
    candidate_ref: ImmutableArtifactRef
    generation_provenance: GuidelineGenerationProvenance
    policy_ref: ImmutableArtifactRef
    fallback_refs: tuple[ImmutableArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class GuideRuntimePreflightOutcome:
    decision: GuideRuntimePreflightDecision
    reason: GuideRuntimePreflightReason | None
    ready_context: ReadyGuideRuntimeContext | None


def _blocked(reason: GuideRuntimePreflightReason) -> GuideRuntimePreflightOutcome:
    return GuideRuntimePreflightOutcome(
        decision=GuideRuntimePreflightDecision.BLOCKED,
        reason=reason,
        ready_context=None,
    )


def _is_valid_request(request: object) -> bool:
    if type(request) is not GuideRuntimePreflightRequest:
        return False
    if type(request.approval_pack) is not Rag15ApprovalPack:
        return False
    if type(request.policy) is not VersionedGuidelinePolicy:
        return False
    if type(request.fallbacks) is not tuple or not request.fallbacks:
        return False
    return True


def _canonical_fallback_bindings(
    fallbacks: tuple[ApprovedGuidelineFallback, ...],
) -> tuple[tuple[str, ImmutableArtifactRef], ...] | None:
    """Canonicalizes runtime fallbacks to code-sorted (code, ref) pairs; None if unusable.

    Input order carries no meaning. Duplicate codes and unsupported item types are
    unusable and must be rejected by the caller.
    """
    bindings: dict[str, ImmutableArtifactRef] = {}
    for item in fallbacks:
        if type(item) is not ApprovedGuidelineFallback:
            return None
        if type(item.code) is not GuidelineFallbackCode:
            return None
        if type(item.artifact_ref) is not ImmutableArtifactRef:
            return None
        if item.code.value in bindings:
            return None
        bindings[item.code.value] = item.artifact_ref
    return tuple(sorted(bindings.items()))


def preflight_guide_runtime(
    request: GuideRuntimePreflightRequest,
    *,
    generator: RuntimeGuidelineGeneratorPort,
    decision_verifier: Rag15ApprovalDecisionVerifierPort,
) -> GuideRuntimePreflightOutcome:
    """Fail-fast, fail-closed preflight of the RAG-15 static runtime candidate.

    Never calls ``generator.generate``.
    """
    # Phase 1 — request shape
    if not _is_valid_request(request):
        return _blocked(GuideRuntimePreflightReason.REQUEST_INVALID)

    pack = request.approval_pack

    # Phase 2 — RAG-15 Approval Pack formal consumability
    verification = verify_rag15_approval_pack(pack, decision_verifier=decision_verifier)
    if not (
        verification.integrity_verified
        and verification.approval_status == "APPROVED"
        and verification.approval_evidence_verified
        and verification.production_consumable
    ):
        return _blocked(GuideRuntimePreflightReason.APPROVAL_PACK_NOT_CONSUMABLE)

    # Phase 3 — actual generator runtime identity, bound as a whole
    runtime_provenance = getattr(generator, "provenance", None)
    if type(runtime_provenance) is not GuidelineGenerationProvenance:
        return _blocked(GuideRuntimePreflightReason.GENERATOR_PROVENANCE_MISMATCH)
    if runtime_provenance != pack.generation_provenance:
        return _blocked(GuideRuntimePreflightReason.GENERATOR_PROVENANCE_MISMATCH)

    # Phase 4 — policy exact-match
    if request.policy.artifact_ref != pack.policy_ref:
        return _blocked(GuideRuntimePreflightReason.POLICY_REF_MISMATCH)

    # Phase 5 — fallback set exact-match, order-independent
    runtime_bindings = _canonical_fallback_bindings(request.fallbacks)
    if runtime_bindings is None:
        return _blocked(GuideRuntimePreflightReason.FALLBACK_SET_MISMATCH)
    approved_bindings = tuple(sorted((pin.code.value, pin.artifact_ref) for pin in pack.fallback_pins))
    if runtime_bindings != approved_bindings:
        return _blocked(GuideRuntimePreflightReason.FALLBACK_SET_MISMATCH)

    return GuideRuntimePreflightOutcome(
        decision=GuideRuntimePreflightDecision.READY,
        reason=None,
        ready_context=ReadyGuideRuntimeContext(
            approval_pack_ref=pack.pack_ref,
            candidate_ref=pack.candidate_ref,
            generation_provenance=runtime_provenance,
            policy_ref=request.policy.artifact_ref,
            fallback_refs=tuple(ref for _, ref in approved_bindings),
        ),
    )
