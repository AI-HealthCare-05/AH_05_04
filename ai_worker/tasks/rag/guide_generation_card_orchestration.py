"""#180 Slice 2 — Guide Generator -> Guideline Card orchestration (#787).

Slice 1 (`guide_orchestration.orchestrate_guide_preflight_handoff`) stops at
READY_FOR_GENERATION. This module consumes that result and carries one request the
rest of the way to a `GuidelineCardOutcome`:

    #765 READY_FOR_GENERATION
      -> #774 production evidence projection
      -> GuidelineGenerationRequest
      -> await generator.generate()
      -> GuidelineCardDraft | GuidelineGenerationFailure
      -> #781 dynamic / static approval authority
      -> GuidelineCardRequest
      -> finalize_guideline_card()
      -> GuidelineCardOutcome

Scope & Authority Boundaries:
- Slice 1 Unchanged: `orchestrate_guide_preflight_handoff()` is called, never
  reimplemented, and its semantics are untouched. The Slice 1 module still never
  reaches the Generator or the Card; this module is the only place that does.
- Sequencing Only: no upstream verdict is re-derived or re-interpreted. A REQUEST
  stop, a #729 BLOCKED preflight and a #760 REJECTED handoff are carried verbatim in
  `upstream_outcome`, and the Generator is not called on any of those paths.
- No Duplicate Failure Mapping: `GuidelineGenerationFailure` is handed to
  `finalize_guideline_card()` untouched. The mapping from PROVIDER_TIMEOUT /
  DEPENDENCY_UNAVAILABLE / VALIDATION_FAILED / PRESCRIPTION_STALE /
  EXECUTION_CONTEXT_STALE / UNSUPPORTED_REQUEST to a Card status, reason and fallback
  code stays in the finalizer, which is its single canonical owner. No fallback is
  selected, remapped or synthesized here.
- No Synthetic Draft: a generation failure never produces a fake, empty or
  placeholder `GuidelineCardDraft`, and never reaches the #781 dynamic binding
  authority. It uses the static #729 policy/fallback approval bridge instead.
- Fail Closed on Authority: if #781 refuses to issue an authority for a draft, this
  stops at DYNAMIC_BINDING_AUTHORITY and the finalizer is not called at all. No
  fallback answer is invented to fill the gap.
- Single Canonical Input: policy, fallbacks and generation provenance are not
  accepted as separate inputs. Policy and fallbacks come from
  `upstream_request.preflight_request`, provenance from the #729 READY runtime
  context, so a caller cannot hand the Card a different policy than the one preflight
  approved.
- Legacy Domain Forbidden: RAG-14 `EvidenceGateOutcome`,
  `GatePassedKnowledgeEvidenceSelection` and `canonical_gate_selection_hash()` are not
  imported, reconstructed or converted. `project_guideline_evidence_from_handoff()` is
  the only evidence input path.
- No Public Runtime Status: `GuideGenerationCardDecision` is orchestration-local.
  `PASS | LIMITED | REJECTED | STALE` and the runtime EVIDENCE_INSUFFICIENT /
  EVIDENCE_CONFLICTED / EVIDENCE_STALE mappings have no approved canonical form yet
  and are not invented here.
- Pure Boundary apart from the Generator: this module is async only because
  `GuidelineGeneratorPort.generate` is. It has no DB, repository, network, clock or
  persistence of its own and imports neither `backend.*` nor `sqlalchemy.*`.

Terminal point: `GuidelineCardOutcome`. Claim citation candidates, claim support
verification, Citation Authorization, `DiscardGeneratedContent` wiring, the Release
Gate, the LangGraph graph, Guideline Card persistence, the Guide API and
`PUBLIC_TRACK_F` are all out of scope. A COMPLETED outcome here does not mean
citations were authorized, a release was approved, or a Card was persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ai_worker.tasks.rag.guide_aggregate_evidence import (
    GuideAggregateEvidence,
    project_guideline_evidence_from_aggregate,
)
from ai_worker.tasks.rag.guide_orchestration import (
    GuideOrchestrationDecision,
    GuideOrchestrationOutcome,
    GuideOrchestrationRequest,
    orchestrate_guide_preflight_handoff,
)
from ai_worker.tasks.rag.guide_personalized_composition import compose_personalized_guide
from ai_worker.tasks.rag.guide_runtime_preflight import (
    GuideRuntimePreflightDecision,
    GuideRuntimePreflightOutcome,
    GuideRuntimePreflightRequest,
    ReadyGuideRuntimeContext,
    RuntimeGuidelineGeneratorPort,
    preflight_guide_runtime,
)
from ai_worker.tasks.rag.guideline_approval_pack import Rag15ApprovalDecisionVerifierPort
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardDraft,
    GuidelineCardOutcome,
    GuidelineCardRequest,
    GuidelineGenerationFailure,
    MedicationIdentityRef,
    finalize_guideline_card,
)
from ai_worker.tasks.rag.guideline_evidence_binding_authority import (
    RequestScopedGuidelineAuthorityOutcome,
    build_request_scoped_guideline_authority,
    build_request_scoped_guideline_static_approval_verifier,
)
from ai_worker.tasks.rag.guideline_generator import (
    GuidelineGenerationRequest,
    GuidelineGenerationResult,
)
from ai_worker.tasks.rag.guideline_production_evidence import (
    ProductionGuidelineEvidenceSet,
    project_guideline_evidence_from_handoff,
)

__all__ = [
    "GuideGenerationCardDecision",
    "GuideGenerationCardOrchestrationRequest",
    "GuideGenerationCardOutcome",
    "GuideGenerationCardStage",
    "orchestrate_guide_generation_card",
]


class GuideGenerationCardDecision(StrEnum):
    """#787-local state. Not a public Guide runtime, release or AI Job state.

    COMPLETED means the finalizer produced a `GuidelineCardOutcome`, which may itself
    be a fallback answer. Reading the Card verdict is the caller's job; this decision
    only says whether the run reached the finalizer.
    """

    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class GuideGenerationCardStage(StrEnum):
    """The stage a stopped run did not get past.

    There is deliberately no CARD_FINALIZATION member: reaching
    `finalize_guideline_card()` always yields a `GuidelineCardOutcome`, which is this
    slice's terminal result, so finalization has no stop of its own.
    """

    UPSTREAM = "UPSTREAM"
    GENERATION = "GENERATION"
    DYNAMIC_BINDING_AUTHORITY = "DYNAMIC_BINDING_AUTHORITY"


@dataclass(frozen=True, slots=True)
class GuideGenerationCardOrchestrationRequest:
    """One Guide request's inputs for the whole preflight -> Card run.

    Policy, fallbacks and generation provenance are intentionally absent: re-accepting
    them would let a caller hand the Card a policy or fallback set that #729 never
    approved. Their canonical sources are `upstream_request.preflight_request.policy`,
    `upstream_request.preflight_request.fallbacks` and the #729 READY
    `ReadyGuideRuntimeContext.generation_provenance`.
    """

    upstream_request: GuideOrchestrationRequest
    medication_identities: tuple[MedicationIdentityRef, ...]


@dataclass(frozen=True, slots=True)
class GuideVerifiedAggregateGenerationRequest:
    """The P0-C-only entry after #760 child handoffs were aggregated.

    The aggregate is not caller-provided citation evidence: it is the exact
    generation input bound into the resulting ``GuideGenerationCardOutcome`` for
    the downstream validator to read.  No single child is selected or synthesized.
    """

    preflight_request: GuideRuntimePreflightRequest
    aggregate: GuideAggregateEvidence
    medication_identities: tuple[MedicationIdentityRef, ...]
    evaluation_time: datetime


@dataclass(frozen=True, slots=True)
class GuideGenerationCardOutcome:
    """Every stage's own result, unchanged.

    A `None` field means that stage was never reached, so a caller can tell an
    upstream short circuit from a generation failure from an authority refusal.
    `authority_outcome` is populated only on the draft path: a generation failure has
    no draft and never consults the dynamic binding authority.
    """

    decision: GuideGenerationCardDecision
    stopped_stage: GuideGenerationCardStage | None
    upstream_outcome: GuideOrchestrationOutcome | None
    generation_result: GuidelineGenerationResult | None
    authority_outcome: RequestScopedGuidelineAuthorityOutcome | None
    card_outcome: GuidelineCardOutcome | None
    aggregate: GuideAggregateEvidence | None = None


def _stopped(
    stage: GuideGenerationCardStage,
    *,
    upstream_outcome: GuideOrchestrationOutcome | None,
    generation_result: GuidelineGenerationResult | None = None,
    authority_outcome: RequestScopedGuidelineAuthorityOutcome | None = None,
    aggregate: GuideAggregateEvidence | None = None,
) -> GuideGenerationCardOutcome:
    return GuideGenerationCardOutcome(
        decision=GuideGenerationCardDecision.STOPPED,
        stopped_stage=stage,
        upstream_outcome=upstream_outcome,
        generation_result=generation_result,
        authority_outcome=authority_outcome,
        card_outcome=None,
        aggregate=aggregate,
    )


async def _generate_and_finalize(
    *,
    evidence: ProductionGuidelineEvidenceSet | GuideAggregateEvidence,
    preflight_request: GuideRuntimePreflightRequest,
    preflight_outcome: GuideRuntimePreflightOutcome,
    runtime_context: ReadyGuideRuntimeContext,
    medication_identities: tuple[MedicationIdentityRef, ...],
    evaluation_time: datetime,
    upstream_outcome: GuideOrchestrationOutcome | None,
    aggregate: GuideAggregateEvidence | None,
    generator: RuntimeGuidelineGeneratorPort,
) -> GuideGenerationCardOutcome:
    """Shared existing generator/binding/finalizer path for one or many runs."""
    generation_result = await compose_personalized_guide(
        GuidelineGenerationRequest(
            medication_identities=medication_identities,
            evidence=evidence,
            policy=preflight_request.policy,
        ),
        generator=generator,
    )

    authority_outcome: RequestScopedGuidelineAuthorityOutcome | None = None
    if type(generation_result) is GuidelineCardDraft:
        authority_outcome = build_request_scoped_guideline_authority(
            preflight_outcome,
            evidence=evidence,
            medication_identities=medication_identities,
            draft=generation_result,
        )
        authority = authority_outcome.authority
        if authority is None:
            return _stopped(
                GuideGenerationCardStage.DYNAMIC_BINDING_AUTHORITY,
                upstream_outcome=upstream_outcome,
                generation_result=generation_result,
                authority_outcome=authority_outcome,
                aggregate=aggregate,
            )
        draft: GuidelineCardDraft | None = generation_result
        generation_failure: GuidelineGenerationFailure | None = None
        approval_verifier = authority.approval_verifier
        approved_evidence_bindings = authority.bindings
    elif type(generation_result) is GuidelineGenerationFailure:
        static_verifier = build_request_scoped_guideline_static_approval_verifier(preflight_outcome)
        if static_verifier is None:
            return _stopped(
                GuideGenerationCardStage.GENERATION,
                upstream_outcome=upstream_outcome,
                generation_result=generation_result,
                aggregate=aggregate,
            )
        draft = None
        generation_failure = generation_result
        approval_verifier = static_verifier
        approved_evidence_bindings = ()
    else:
        return _stopped(
            GuideGenerationCardStage.GENERATION,
            upstream_outcome=upstream_outcome,
            generation_result=generation_result,
            aggregate=aggregate,
        )

    card_outcome = finalize_guideline_card(
        GuidelineCardRequest(
            medication_identities=medication_identities,
            evidence=evidence,
            draft=draft,
            generation_failure=generation_failure,
            policy=preflight_request.policy,
            provenance=runtime_context.generation_provenance,
            approved_fallbacks=preflight_request.fallbacks,
            evaluated_at=evaluation_time,
            approved_evidence_bindings=approved_evidence_bindings,
        ),
        approval_verifier=approval_verifier,
    )
    return GuideGenerationCardOutcome(
        decision=GuideGenerationCardDecision.COMPLETED,
        stopped_stage=None,
        upstream_outcome=upstream_outcome,
        generation_result=generation_result,
        authority_outcome=authority_outcome,
        card_outcome=card_outcome,
        aggregate=aggregate,
    )


async def orchestrate_guide_generation_card(
    request: GuideGenerationCardOrchestrationRequest,
    *,
    generator: RuntimeGuidelineGeneratorPort,
    decision_verifier: Rag15ApprovalDecisionVerifierPort,
) -> GuideGenerationCardOutcome:
    """Run Slice 1, then the Generator, then the Guideline Card finalizer.

    `generator.generate` is awaited exactly once, and only after Slice 1 reported
    READY_FOR_GENERATION. Every pre-generation stop leaves the Generator untouched.
    """
    # Phase 1 — Slice 1, unchanged (#765). A foreign request is handed straight to
    # Slice 1's own request-shape guard rather than judged again here, so a malformed
    # run stops at REQUEST with Slice 1's verdict instead of raising.
    upstream_request = request.upstream_request if type(request) is GuideGenerationCardOrchestrationRequest else None
    upstream_outcome = orchestrate_guide_preflight_handoff(
        upstream_request,  # type: ignore[arg-type]
        generator=generator,
        decision_verifier=decision_verifier,
    )
    ready_inputs = upstream_outcome.ready_inputs
    if upstream_outcome.decision is not GuideOrchestrationDecision.READY_FOR_GENERATION or ready_inputs is None:
        return _stopped(GuideGenerationCardStage.UPSTREAM, upstream_outcome=upstream_outcome)
    preflight_outcome = upstream_outcome.preflight_outcome
    if preflight_outcome is None:
        return _stopped(GuideGenerationCardStage.UPSTREAM, upstream_outcome=upstream_outcome)

    # Phase 2 — #774 production evidence projection. The only evidence input.
    production_evidence = project_guideline_evidence_from_handoff(ready_inputs.evidence_handoff)

    preflight_request = request.upstream_request.preflight_request
    return await _generate_and_finalize(
        evidence=production_evidence,
        preflight_request=preflight_request,
        preflight_outcome=preflight_outcome,
        runtime_context=ready_inputs.runtime_context,
        medication_identities=request.medication_identities,
        evaluation_time=production_evidence.evaluated_at,
        upstream_outcome=upstream_outcome,
        aggregate=None,
        generator=generator,
    )


async def orchestrate_guide_generation_card_from_verified_aggregate(
    request: GuideVerifiedAggregateGenerationRequest,
    *,
    generator: RuntimeGuidelineGeneratorPort,
    decision_verifier: Rag15ApprovalDecisionVerifierPort,
) -> GuideGenerationCardOutcome:
    """Reuse the #787 generator/card path after P0-C's verified aggregate boundary."""
    if type(request) is not GuideVerifiedAggregateGenerationRequest:
        return _stopped(GuideGenerationCardStage.UPSTREAM, upstream_outcome=None)
    try:
        evidence = project_guideline_evidence_from_aggregate(request.aggregate)
    except ValueError:
        return _stopped(GuideGenerationCardStage.UPSTREAM, upstream_outcome=None)
    preflight_outcome = preflight_guide_runtime(
        request.preflight_request,
        generator=generator,
        decision_verifier=decision_verifier,
    )
    runtime_context = preflight_outcome.ready_context
    if preflight_outcome.decision is not GuideRuntimePreflightDecision.READY or runtime_context is None:
        return _stopped(GuideGenerationCardStage.UPSTREAM, upstream_outcome=None, aggregate=request.aggregate)
    return await _generate_and_finalize(
        evidence=evidence,
        preflight_request=request.preflight_request,
        preflight_outcome=preflight_outcome,
        runtime_context=runtime_context,
        medication_identities=request.medication_identities,
        evaluation_time=request.evaluation_time,
        upstream_outcome=None,
        aggregate=request.aggregate,
        generator=generator,
    )
