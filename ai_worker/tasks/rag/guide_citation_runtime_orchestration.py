"""Application orchestration for the Guide Citation runtime core slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationBuildDecision,
    CitationAuthorizationBuildOutcome,
    CitationAuthorizationRequest,
    build_citation_authorization_request,
)
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityIssueOutcome,
    CitationAuthorityStorePort,
    CitationEligibilityReaderPort,
    RequestCitationAuthorityReaderPort,
    RequestGuardRuntimeBindingReaderPort,
    RuntimeBundleCitationApprovalReaderPort,
    SourceUseApprovalExactReaderPort,
    issue_citation_authorization,
)
from ai_worker.tasks.rag.citation_finalization_wiring import finalize_citation_authority_outcome
from ai_worker.tasks.rag.citation_finalizer import CitationFinalizationOutcome
from ai_worker.tasks.rag.guide_claim_citation_validation import (
    GuideClaimCitationDecision,
    GuideClaimCitationValidationOutcome,
    GuideClaimCitationValidationRequest,
    run_guide_claim_citation_validation,
)
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOrchestrationRequest,
    GuideGenerationCardOutcome,
    orchestrate_guide_generation_card,
)
from ai_worker.tasks.rag.guide_runtime_preflight import RuntimeGuidelineGeneratorPort
from ai_worker.tasks.rag.guideline_approval_pack import Rag15ApprovalDecisionVerifierPort
from ai_worker.tasks.rag.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingAssemblyError,
    build_origin_request_guard_binding,
    build_runtime_authorization_binding,
)
from rag_runtime.request_guard_runtime_binding import RequestGuardRuntimeBindingRef

__all__ = [
    "GuideCitationRuntimeDecision",
    "GuideCitationRuntimeOrchestrationOutcome",
    "GuideCitationRuntimeOrchestrationRequest",
    "GuideCitationRuntimeStage",
    "orchestrate_guide_citation_runtime",
]


class GuideCitationRuntimeDecision(StrEnum):
    """Internal completion state; COMPLETED does not imply authorization or release."""

    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class GuideCitationRuntimeStage(StrEnum):
    GENERATION_CARD = "GENERATION_CARD"
    CLAIM_CITATION_VALIDATION = "CLAIM_CITATION_VALIDATION"
    REQUEST_GUARD_RUNTIME_BINDING = "REQUEST_GUARD_RUNTIME_BINDING"
    CITATION_AUTHORIZATION_REQUEST = "CITATION_AUTHORIZATION_REQUEST"


@dataclass(frozen=True, slots=True)
class GuideCitationRuntimeOrchestrationRequest:
    generation_request: GuideGenerationCardOrchestrationRequest
    request_guard_runtime_binding_ref: RequestGuardRuntimeBindingRef
    evaluation_time: datetime

    def __post_init__(self) -> None:
        if self.evaluation_time.tzinfo is None or self.evaluation_time.utcoffset() is None:
            raise ValueError("evaluation_time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class GuideCitationRuntimeOrchestrationOutcome:
    decision: GuideCitationRuntimeDecision
    stopped_stage: GuideCitationRuntimeStage | None
    generation_outcome: GuideGenerationCardOutcome
    claim_citation_outcome: GuideClaimCitationValidationOutcome | None
    authorization_build_outcome: CitationAuthorizationBuildOutcome | None
    authorization_request: CitationAuthorizationRequest | None
    authority_outcome: CitationAuthorityIssueOutcome | None
    finalization_outcome: CitationFinalizationOutcome | None


def _stopped(
    stage: GuideCitationRuntimeStage,
    *,
    generation_outcome: GuideGenerationCardOutcome,
    claim_citation_outcome: GuideClaimCitationValidationOutcome | None = None,
    authorization_build_outcome: CitationAuthorizationBuildOutcome | None = None,
) -> GuideCitationRuntimeOrchestrationOutcome:
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.STOPPED,
        stopped_stage=stage,
        generation_outcome=generation_outcome,
        claim_citation_outcome=claim_citation_outcome,
        authorization_build_outcome=authorization_build_outcome,
        authorization_request=None,
        authority_outcome=None,
        finalization_outcome=None,
    )


async def orchestrate_guide_citation_runtime(
    request: GuideCitationRuntimeOrchestrationRequest,
    *,
    generator: RuntimeGuidelineGeneratorPort,
    decision_verifier: Rag15ApprovalDecisionVerifierPort,
    guard_reader: RequestGuardRuntimeBindingReaderPort,
    request_authority_reader: RequestCitationAuthorityReaderPort,
    pin_reader: RuntimeBundleCitationApprovalReaderPort,
    approval_reader: SourceUseApprovalExactReaderPort,
    eligibility_reader: CitationEligibilityReaderPort,
    store: CitationAuthorityStorePort,
) -> GuideCitationRuntimeOrchestrationOutcome:
    """Sequence existing Guide, Citation authority, and finalization boundaries."""

    generation_outcome = await orchestrate_guide_generation_card(
        request.generation_request,
        generator=generator,
        decision_verifier=decision_verifier,
    )
    if generation_outcome.decision is GuideGenerationCardDecision.STOPPED:
        return _stopped(
            GuideCitationRuntimeStage.GENERATION_CARD,
            generation_outcome=generation_outcome,
        )

    claim_citation_outcome = run_guide_claim_citation_validation(
        GuideClaimCitationValidationRequest(guide_outcome=generation_outcome)
    )
    validated_selection = claim_citation_outcome.validated_selection
    if claim_citation_outcome.decision is GuideClaimCitationDecision.STOPPED or validated_selection is None:
        return _stopped(
            GuideCitationRuntimeStage.CLAIM_CITATION_VALIDATION,
            generation_outcome=generation_outcome,
            claim_citation_outcome=claim_citation_outcome,
        )

    guard_observation = await guard_reader.read_exact(request.request_guard_runtime_binding_ref)
    if guard_observation is None:
        return _stopped(
            GuideCitationRuntimeStage.REQUEST_GUARD_RUNTIME_BINDING,
            generation_outcome=generation_outcome,
            claim_citation_outcome=claim_citation_outcome,
        )
    try:
        runtime_binding = build_runtime_authorization_binding(guard_observation)
        origin_request_guard = build_origin_request_guard_binding(guard_observation)
    except RequestGuardRuntimeBindingAssemblyError:
        return _stopped(
            GuideCitationRuntimeStage.REQUEST_GUARD_RUNTIME_BINDING,
            generation_outcome=generation_outcome,
            claim_citation_outcome=claim_citation_outcome,
        )

    authorization_build_outcome = build_citation_authorization_request(
        validated_selection,
        runtime_binding,
        origin_request_guard,
    )
    authorization_request = authorization_build_outcome.request
    if authorization_build_outcome.decision is not AuthorizationBuildDecision.BUILT or authorization_request is None:
        return _stopped(
            GuideCitationRuntimeStage.CITATION_AUTHORIZATION_REQUEST,
            generation_outcome=generation_outcome,
            claim_citation_outcome=claim_citation_outcome,
            authorization_build_outcome=authorization_build_outcome,
        )

    authority_outcome = await issue_citation_authorization(
        validated_selection=validated_selection,
        request=authorization_request,
        evaluation_time=request.evaluation_time,
        guard_reader=guard_reader,
        request_authority_reader=request_authority_reader,
        pin_reader=pin_reader,
        approval_reader=approval_reader,
        eligibility_reader=eligibility_reader,
        store=store,
    )
    finalization_outcome = finalize_citation_authority_outcome(
        validated_selection=validated_selection,
        authorization_request=authorization_request,
        authority_outcome=authority_outcome,
    )
    return GuideCitationRuntimeOrchestrationOutcome(
        decision=GuideCitationRuntimeDecision.COMPLETED,
        stopped_stage=None,
        generation_outcome=generation_outcome,
        claim_citation_outcome=claim_citation_outcome,
        authorization_build_outcome=authorization_build_outcome,
        authorization_request=authorization_request,
        authority_outcome=authority_outcome,
        finalization_outcome=finalization_outcome,
    )
