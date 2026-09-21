"""Canonical Guide citation-runtime to shared release-projection composition."""

from __future__ import annotations

from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityStorePort,
    CitationEligibilityReaderPort,
    RequestCitationAuthorityReaderPort,
    RequestGuardRuntimeBindingReaderPort,
    RuntimeBundleCitationApprovalReaderPort,
    SourceUseApprovalExactReaderPort,
)
from ai_worker.tasks.rag.guide_citation_runtime_orchestration import (
    GuideCitationRuntimeOrchestrationRequest,
    orchestrate_guide_citation_runtime,
)
from ai_worker.tasks.rag.guide_release_projection import project_guide_runtime_release
from ai_worker.tasks.rag.guide_runtime_preflight import RuntimeGuidelineGeneratorPort
from ai_worker.tasks.rag.guide_runtime_release import finalize_guide_runtime_release
from ai_worker.tasks.rag.guideline_approval_pack import Rag15ApprovalDecisionVerifierPort
from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionOutcome

__all__ = ["execute_guide_runtime_release"]


async def execute_guide_runtime_release(
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
) -> GuideRuntimeReleaseProjectionOutcome:
    """Execute one authoritative Guide runtime and return its shared release projection."""

    citation_runtime_outcome = await orchestrate_guide_citation_runtime(
        request,
        generator=generator,
        decision_verifier=decision_verifier,
        guard_reader=guard_reader,
        request_authority_reader=request_authority_reader,
        pin_reader=pin_reader,
        approval_reader=approval_reader,
        eligibility_reader=eligibility_reader,
        store=store,
    )
    release_result = finalize_guide_runtime_release(citation_runtime_outcome)
    return project_guide_runtime_release(release_result)
