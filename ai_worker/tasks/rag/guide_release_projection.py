"""Canonical projection from #893 release output to the shared Backend carrier.

The existing Guide Claim/Citation validation, authorization, and finalization
kernels own authority semantics. This adapter never replays those kernels or
compares a Card citation against upstream evidence or receipts. For PASS it
checks only that the release Card and the already authorized selection refer to
the same Card target, then transports approved text and selected citations.
"""

from __future__ import annotations

from uuid import UUID

from ai_worker.tasks.rag.citation_finalizer import AuthorizedCitationSelection
from ai_worker.tasks.rag.claim_citation_validator import CitationCandidate, LifestyleGuidelineEvidenceRef
from ai_worker.tasks.rag.guide_runtime_release import (
    GuideRuntimeEvidenceStatus,
    GuideRuntimeExecutionStatus,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseResult,
    _is_valid_release_result,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardReason,
    GuidelineCardStatus,
    GuidelineFallbackCode,
    VerifiedGuidelineFallback,
)
from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeApprovedAnswer,
    GuideRuntimeApprovedFallback,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionOutcome,
    GuideRuntimeReleaseProjectionUnavailable,
    GuideRuntimeVerifiedCitation,
)
from rag_runtime.guide_release_projection import (
    GuideRuntimeReleaseDecision as SharedReleaseDecision,
)

__all__ = ["project_guide_runtime_release"]

_FALLBACK_CODE_MAP = {
    GuidelineFallbackCode.NO_APPROVED_EVIDENCE: GuideRuntimeFallbackCode.NO_APPROVED_EVIDENCE,
    GuidelineFallbackCode.CONFLICTING_EVIDENCE: GuideRuntimeFallbackCode.CONFLICTING_EVIDENCE,
    GuidelineFallbackCode.PROVIDER_TIMEOUT: GuideRuntimeFallbackCode.PROVIDER_TIMEOUT,
    GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE: GuideRuntimeFallbackCode.DEPENDENCY_UNAVAILABLE,
    GuidelineFallbackCode.VALIDATION_FAILED: GuideRuntimeFallbackCode.VALIDATION_FAILED,
    GuidelineFallbackCode.PRESCRIPTION_STALE: GuideRuntimeFallbackCode.PRESCRIPTION_STALE,
    GuidelineFallbackCode.EXECUTION_CONTEXT_STALE: GuideRuntimeFallbackCode.EXECUTION_CONTEXT_STALE,
    GuidelineFallbackCode.UNSUPPORTED_REQUEST: GuideRuntimeFallbackCode.UNSUPPORTED_REQUEST,
}

_SHARED_DECISION_MAP = {
    GuideRuntimeReleaseDecision.PASS: SharedReleaseDecision.PASS,
    GuideRuntimeReleaseDecision.LIMITED: SharedReleaseDecision.LIMITED,
    GuideRuntimeReleaseDecision.REJECTED: SharedReleaseDecision.REJECTED,
    GuideRuntimeReleaseDecision.STALE: SharedReleaseDecision.STALE,
}


def project_guide_runtime_release(result: object) -> GuideRuntimeReleaseProjectionOutcome:
    """Return the sole shared projection of one #893 release result, fail closed."""

    if type(result) is not GuideRuntimeReleaseResult or not _is_valid_release_result(result):
        return _unavailable()
    try:
        if result.release_decision is GuideRuntimeReleaseDecision.PASS:
            return _project_pass(result)
        return _project_approved_fallback(result)
    except (AttributeError, IndexError, TypeError, ValueError):
        return _unavailable()


def _project_pass(result: GuideRuntimeReleaseResult) -> GuideRuntimeReleaseProjectionOutcome:
    if (
        result.execution_status is not GuideRuntimeExecutionStatus.SUCCEEDED
        or result.evidence_status is not GuideRuntimeEvidenceStatus.SUFFICIENT
        or not result.is_current
        or result.fallback_code is not None
        or type(result.authorized_selection) is not AuthorizedCitationSelection
        or result.card_outcome is None
        or result.card_outcome.status is not GuidelineCardStatus.GENERATED
        or result.card_outcome.reason is not GuidelineCardReason.CARD_GENERATED
        or result.card_outcome.fallback is not None
        or result.card_outcome.fallback_code is not None
        or result.card_outcome.card is None
    ):
        return _unavailable()

    card = result.card_outcome.card
    selection = result.authorized_selection.validated_selection
    candidate_set = selection.candidate_set
    if card.artifact_ref.content_sha256 != candidate_set.target.target_ref:
        return _unavailable()

    answer = GuideRuntimeApprovedAnswer(
        claim_action_texts=tuple(claim.action_text.reveal() for claim in card.claims),
        uncertainty_text=card.uncertainty_text.reveal(),
        consultation_text=card.consultation_text.reveal(),
    )
    citations = tuple(
        _project_citation(candidate, card_target_ref=candidate_set.target.target_ref)
        for candidate in candidate_set.citations
    )
    return GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
        release_decision=_SHARED_DECISION_MAP[result.release_decision],
        is_current=result.is_current,
        answer=answer,
        fallback=None,
        citations=citations,
    )


def _project_citation(candidate: CitationCandidate, *, card_target_ref: str) -> GuideRuntimeVerifiedCitation:
    """Losslessly expose fields from an already validated candidate citation."""

    evidence_ref = candidate.evidence_ref
    if type(evidence_ref) is not LifestyleGuidelineEvidenceRef:
        raise ValueError("Guide release projection requires lifestyle guideline evidence")
    snapshot_id, member_id, evidence_key = evidence_ref.guideline_evidence_ref.split("/", maxsplit=2)
    return GuideRuntimeVerifiedCitation(
        card_target_ref=card_target_ref,
        claim_key=candidate.claim_key,
        evidence_key=evidence_key,
        source_type=candidate.source_type.value,
        source_snapshot_id=UUID(snapshot_id),
        source_snapshot_member_id=UUID(member_id),
        source_code=evidence_ref.execution_provenance.source_code,
        source_version=evidence_ref.source_version,
        locator=evidence_ref.locator,
        content_sha256=evidence_ref.content_sha256,
        display_order=candidate.display_order,
    )


def _project_approved_fallback(result: GuideRuntimeReleaseResult) -> GuideRuntimeReleaseProjectionOutcome:
    if (
        result.release_decision
        not in {
            GuideRuntimeReleaseDecision.LIMITED,
            GuideRuntimeReleaseDecision.REJECTED,
            GuideRuntimeReleaseDecision.STALE,
        }
        or result.authorized_selection is not None
        or result.card_outcome is None
        or result.card_outcome.card is not None
        or type(result.card_outcome.fallback) is not VerifiedGuidelineFallback
        or type(result.fallback_code) is not GuidelineFallbackCode
        or result.card_outcome.fallback_code is not result.fallback_code
        or result.card_outcome.fallback.code is not result.fallback_code
    ):
        return _unavailable()
    fallback_code = _FALLBACK_CODE_MAP.get(result.fallback_code)
    if fallback_code is None:
        return _unavailable()
    return GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
        release_decision=_SHARED_DECISION_MAP[result.release_decision],
        is_current=result.is_current,
        answer=None,
        fallback=GuideRuntimeApprovedFallback(code=fallback_code, text=result.card_outcome.fallback.text.reveal()),
        citations=(),
    )


def _unavailable() -> GuideRuntimeReleaseProjectionUnavailable:
    return GuideRuntimeReleaseProjectionUnavailable()
