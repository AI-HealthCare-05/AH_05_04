"""Canonical Backend-facing Guide runtime assembly.

This module has two composition seams:

* ``execute_guide_runtime_release`` retains the existing citation-runtime -> release
  projection seam (#918).
* ``execute_canonical_guide_runtime`` is the one Backend-callable assembly from the
  verified Guide request carrier through per-medication retrieval, hydration,
  authoritative handoff, aggregate evidence, the existing citation runtime, and
  that #918 seam.

Neither seam reimplements an upstream decision, receipt, handoff, policy, ranking,
generator, citation authority, or release projection.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unicodedata import normalize
from uuid import UUID

from ai_worker.tasks.rag.assessment_eligibility_authority import AssessmentEligibilityAuthorityReaderPort
from ai_worker.tasks.rag.authoritative_guide_evidence_handoff import (
    AuthoritativeGuideEvidenceAssemblyDecision,
    AuthoritativeGuideEvidenceHandoffAssemblyRequest,
    assemble_authoritative_guide_evidence_handoff,
)
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityStorePort,
    CitationEligibilityReaderPort,
    RequestCitationAuthorityReaderPort,
    RequestGuardRuntimeBindingReaderPort,
    RuntimeBundleCitationApprovalReaderPort,
    SourceUseApprovalExactReaderPort,
)
from ai_worker.tasks.rag.guide_aggregate_evidence import (
    GuideAggregateEvidenceAssemblyDecision,
    GuideAggregateEvidenceEntry,
    assemble_guide_aggregate_evidence,
)
from ai_worker.tasks.rag.guide_citation_runtime_orchestration import (
    GuideCitationRuntimeOrchestrationRequest,
    orchestrate_guide_citation_runtime,
)
from ai_worker.tasks.rag.guide_generation_card_orchestration import GuideVerifiedAggregateGenerationRequest
from ai_worker.tasks.rag.guide_medication_guidance_retrieval import (
    GuideMedicationGuidanceRetrievalDecision,
    GuideMedicationGuidanceRetrievalDependencies,
    GuideMedicationGuidanceRetrievalRequest,
    GuideRuntimeRequestCarrierPort,
    retrieve_medication_guidance,
)
from ai_worker.tasks.rag.guide_medication_identity_resolution import (
    MedicationIdentityRefResolution,
    MedicationIdentityRefResolverPort,
)
from ai_worker.tasks.rag.guide_release_projection import project_guide_runtime_release
from ai_worker.tasks.rag.guide_runtime_preflight import GuideRuntimePreflightRequest, RuntimeGuidelineGeneratorPort
from ai_worker.tasks.rag.guide_runtime_release import finalize_guide_runtime_release
from ai_worker.tasks.rag.guideline_approval_pack import Rag15ApprovalDecisionVerifierPort
from ai_worker.tasks.rag.guideline_card import MedicationIdentityRef
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import (
    GuideContentHydrationDecision,
    KnowledgeChunkContentReaderPort,
    hydrate_guide_retrieval_content,
)
from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionOutcome

__all__ = ["execute_canonical_guide_runtime", "execute_guide_runtime_release"]


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


def _unavailable() -> GuideRuntimeReleaseProjectionOutcome:
    """Return the existing content-free release boundary for an upstream stop."""
    from rag_runtime.guide_release_projection import GuideRuntimeReleaseProjectionUnavailable

    return GuideRuntimeReleaseProjectionUnavailable()


def _is_utc(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _medication_identities(
    runtime_request: GuideRuntimeRequestCarrierPort,
    resolutions: tuple[MedicationIdentityRefResolution, ...] | None,
) -> tuple[MedicationIdentityRef, ...] | None:
    """Validate and project the resolver's existing matched-identity observations."""
    if type(resolutions) is not tuple or len(resolutions) != len(runtime_request.identifications):
        return None

    identities: list[MedicationIdentityRef] = []
    seen_identifications: set[UUID] = set()
    seen_medications: set[UUID] = set()
    seen_product_identities: set[tuple[str, str]] = set()
    for identification, resolution in zip(runtime_request.identifications, resolutions, strict=True):
        medication_identification_id = getattr(identification, "medication_identification_id", None)
        if type(resolution) is not MedicationIdentityRefResolution:
            return None
        medication_identity = resolution.medication_identity
        code_system, canonical_code = medication_identity.code_system, medication_identity.canonical_code
        if (
            type(medication_identification_id) is not UUID
            or resolution.medication_identification_id != medication_identification_id
            or resolution.prescription_version_medication_id != identification.prescription_version_medication_id
            or medication_identity.prescription_version_medication_id
            != str(identification.prescription_version_medication_id)
            or resolution.medication_identification_id in seen_identifications
            or resolution.prescription_version_medication_id in seen_medications
            or type(code_system) is not str
            or not code_system.strip()
            or code_system != code_system.strip()
            or normalize("NFC", code_system) != code_system
            or type(canonical_code) is not str
            or not canonical_code.strip()
            or canonical_code != canonical_code.strip()
            or normalize("NFC", canonical_code) != canonical_code
            or (code_system, canonical_code) in seen_product_identities
        ):
            return None
        seen_identifications.add(resolution.medication_identification_id)
        seen_medications.add(resolution.prescription_version_medication_id)
        seen_product_identities.add((code_system, canonical_code))
        identities.append(medication_identity)
    return tuple(identities)


async def execute_canonical_guide_runtime(  # noqa: C901 - ordered fail-closed composition
    runtime_request: GuideRuntimeRequestCarrierPort,
    *,
    guide_preflight_request: GuideRuntimePreflightRequest,
    evaluation_time: datetime,
    retrieval_dependencies: GuideMedicationGuidanceRetrievalDependencies,
    medication_identity_resolver: MedicationIdentityRefResolverPort,
    content_reader: KnowledgeChunkContentReaderPort,
    evidence_authority_reader: AssessmentEligibilityAuthorityReaderPort,
    generator: RuntimeGuidelineGeneratorPort,
    decision_verifier: Rag15ApprovalDecisionVerifierPort,
    guard_reader: RequestGuardRuntimeBindingReaderPort,
    request_authority_reader: RequestCitationAuthorityReaderPort,
    pin_reader: RuntimeBundleCitationApprovalReaderPort,
    approval_reader: SourceUseApprovalExactReaderPort,
    eligibility_reader: CitationEligibilityReaderPort,
    store: CitationAuthorityStorePort,
) -> GuideRuntimeReleaseProjectionOutcome:
    """Run one Guide carrier through the existing authoritative runtime boundaries.

    Every upstream stop returns the existing content-free projection.  No local
    fallback, release-decision mapping, synthetic receipt, or aggregate handoff is
    created here.
    """
    if not _is_utc(evaluation_time):
        return _unavailable()

    retrieval_outcome = await retrieve_medication_guidance(
        GuideMedicationGuidanceRetrievalRequest(runtime_request=runtime_request),
        dependencies=retrieval_dependencies,
    )
    if retrieval_outcome.decision is not GuideMedicationGuidanceRetrievalDecision.READY:
        return _unavailable()

    resolutions = await medication_identity_resolver.resolve_ordered(identifications=runtime_request.identifications)
    medication_identities = _medication_identities(runtime_request, resolutions)
    if medication_identities is None or len(medication_identities) != len(retrieval_outcome.medications):
        return _unavailable()

    aggregate_entries: list[GuideAggregateEvidenceEntry] = []
    for medication, medication_identity in zip(retrieval_outcome.medications, medication_identities, strict=True):
        hydration_outcome = await hydrate_guide_retrieval_content(
            medication.composition.selections,
            reader=content_reader,
        )
        if hydration_outcome.decision is not GuideContentHydrationDecision.HYDRATED:
            return _unavailable()

        persisted_receipt = medication.hybrid_outcome.persisted_receipt
        retrieval_receipt = medication.composition.retrieval_receipt
        if persisted_receipt is None or retrieval_receipt is None:
            return _unavailable()
        authorities = []
        for hydrated_selection in hydration_outcome.selections:
            authority = await evidence_authority_reader.read_by_selection(
                retrieval_run_id=persisted_receipt.run_id,
                knowledge_chunk_id=hydrated_selection.selection.hit.provenance.knowledge_chunk_id,
            )
            if authority is None:
                return _unavailable()
            authorities.append(authority)

        handoff_outcome = assemble_authoritative_guide_evidence_handoff(
            AuthoritativeGuideEvidenceHandoffAssemblyRequest(
                persisted_retrieval_receipt=persisted_receipt,
                retrieval_receipt=retrieval_receipt,
                hydrated_selections=hydration_outcome.selections,
                authorities=tuple(authorities),
                evaluated_at=evaluation_time,
            )
        )
        if (
            handoff_outcome.decision is not AuthoritativeGuideEvidenceAssemblyDecision.BUILT
            or handoff_outcome.build_outcome is None
            or handoff_outcome.build_outcome.handoff is None
        ):
            return _unavailable()
        aggregate_entries.append(
            GuideAggregateEvidenceEntry(
                medication_identity=medication_identity,
                retrieval_run_id=persisted_receipt.run_id,
                handoff=handoff_outcome.build_outcome.handoff,
            )
        )

    aggregate_outcome = assemble_guide_aggregate_evidence(tuple(aggregate_entries))
    if (
        aggregate_outcome.decision is not GuideAggregateEvidenceAssemblyDecision.ASSEMBLED
        or aggregate_outcome.aggregate is None
    ):
        return _unavailable()

    return await execute_guide_runtime_release(
        GuideCitationRuntimeOrchestrationRequest(
            generation_request=GuideVerifiedAggregateGenerationRequest(
                preflight_request=guide_preflight_request,
                aggregate=aggregate_outcome.aggregate,
                medication_identities=medication_identities,
                evaluation_time=evaluation_time,
            ),
            request_guard_runtime_binding_ref=runtime_request.request_guard_runtime_binding_ref,
            evaluation_time=evaluation_time,
        ),
        generator=generator,
        decision_verifier=decision_verifier,
        guard_reader=guard_reader,
        request_authority_reader=request_authority_reader,
        pin_reader=pin_reader,
        approval_reader=approval_reader,
        eligibility_reader=eligibility_reader,
        store=store,
    )
