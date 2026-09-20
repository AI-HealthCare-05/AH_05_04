"""Deterministic Guide runtime release/fallback projection.

This pure kernel consumes the authoritative Guide Citation runtime outcome and
projects only values already established by the Safety Result v2 target and the
Guideline Card. It does not produce a Backend DTO, re-check Citation authority,
copy generated/source text, or synthesize fallback content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.citation_finalizer import AuthorizedCitationSelection
from ai_worker.tasks.rag.guide_citation_runtime_orchestration import (
    GuideCitationRuntimeDecision,
    GuideCitationRuntimeOrchestrationOutcome,
    GuideCitationRuntimeStage,
)
from ai_worker.tasks.rag.guide_generation_card_orchestration import (
    GuideGenerationCardDecision,
    GuideGenerationCardOutcome,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineCardOutcome,
    GuidelineCardReason,
    GuidelineCardStatus,
    GuidelineFallbackCode,
    VerifiedGuidelineFallback,
)

__all__ = [
    "GuideRuntimeEvidenceStatus",
    "GuideRuntimeExecutionStatus",
    "GuideRuntimeReleaseDecision",
    "GuideRuntimeReleaseResult",
    "finalize_guide_runtime_release",
]


class GuideRuntimeExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    NO_RESULT = "NO_RESULT"
    TIMED_OUT = "TIMED_OUT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class GuideRuntimeEvidenceStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"


class GuideRuntimeReleaseDecision(StrEnum):
    PASS = "PASS"
    LIMITED = "LIMITED"
    REJECTED = "REJECTED"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class GuideRuntimeReleaseResult:
    execution_status: GuideRuntimeExecutionStatus | None
    evidence_status: GuideRuntimeEvidenceStatus | None
    release_decision: GuideRuntimeReleaseDecision
    is_current: bool
    fallback_code: GuidelineFallbackCode | None
    authorized_selection: AuthorizedCitationSelection | None
    card_outcome: GuidelineCardOutcome | None

    def __post_init__(self) -> None:
        if not _is_valid_release_result(self):
            raise ValueError("invalid Guide runtime release result")


@dataclass(frozen=True, slots=True)
class _FallbackProjection:
    execution_status: GuideRuntimeExecutionStatus | None
    evidence_status: GuideRuntimeEvidenceStatus | None
    release_decision: GuideRuntimeReleaseDecision
    is_current: bool


_FALLBACK_PROJECTIONS = {
    (
        GuidelineCardStatus.NO_RESULT,
        GuidelineCardReason.EVIDENCE_INSUFFICIENT,
        GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
    ): _FallbackProjection(
        GuideRuntimeExecutionStatus.NO_RESULT,
        GuideRuntimeEvidenceStatus.INSUFFICIENT,
        GuideRuntimeReleaseDecision.REJECTED,
        True,
    ),
    (
        GuidelineCardStatus.NO_RESULT,
        GuidelineCardReason.EVIDENCE_STALE,
        GuidelineFallbackCode.NO_APPROVED_EVIDENCE,
    ): _FallbackProjection(
        GuideRuntimeExecutionStatus.NO_RESULT,
        GuideRuntimeEvidenceStatus.STALE,
        GuideRuntimeReleaseDecision.REJECTED,
        True,
    ),
    (
        GuidelineCardStatus.NO_RESULT,
        GuidelineCardReason.EVIDENCE_CONFLICTED,
        GuidelineFallbackCode.CONFLICTING_EVIDENCE,
    ): _FallbackProjection(
        GuideRuntimeExecutionStatus.NO_RESULT,
        GuideRuntimeEvidenceStatus.CONFLICTED,
        GuideRuntimeReleaseDecision.REJECTED,
        True,
    ),
    (
        GuidelineCardStatus.NO_RESULT,
        GuidelineCardReason.PROVIDER_TIMEOUT,
        GuidelineFallbackCode.PROVIDER_TIMEOUT,
    ): _FallbackProjection(
        GuideRuntimeExecutionStatus.TIMED_OUT,
        None,
        GuideRuntimeReleaseDecision.REJECTED,
        True,
    ),
    (
        GuidelineCardStatus.NO_RESULT,
        GuidelineCardReason.DEPENDENCY_UNAVAILABLE,
        GuidelineFallbackCode.DEPENDENCY_UNAVAILABLE,
    ): _FallbackProjection(
        GuideRuntimeExecutionStatus.DEPENDENCY_ERROR,
        None,
        GuideRuntimeReleaseDecision.REJECTED,
        True,
    ),
    (
        GuidelineCardStatus.VALIDATION_REJECTED,
        GuidelineCardReason.VALIDATION_FAILED,
        GuidelineFallbackCode.VALIDATION_FAILED,
    ): _FallbackProjection(
        GuideRuntimeExecutionStatus.VALIDATION_ERROR,
        None,
        GuideRuntimeReleaseDecision.REJECTED,
        True,
    ),
    (
        GuidelineCardStatus.STALE,
        GuidelineCardReason.PRESCRIPTION_STALE,
        GuidelineFallbackCode.PRESCRIPTION_STALE,
    ): _FallbackProjection(None, None, GuideRuntimeReleaseDecision.STALE, False),
    (
        GuidelineCardStatus.STALE,
        GuidelineCardReason.EXECUTION_CONTEXT_STALE,
        GuidelineFallbackCode.EXECUTION_CONTEXT_STALE,
    ): _FallbackProjection(None, None, GuideRuntimeReleaseDecision.STALE, False),
    (
        GuidelineCardStatus.LIMITED,
        GuidelineCardReason.UNSUPPORTED_REQUEST,
        GuidelineFallbackCode.UNSUPPORTED_REQUEST,
    ): _FallbackProjection(
        GuideRuntimeExecutionStatus.SUCCEEDED,
        None,
        GuideRuntimeReleaseDecision.LIMITED,
        True,
    ),
}


def _is_valid_release_result(result: GuideRuntimeReleaseResult) -> bool:
    if (
        (result.execution_status is not None and type(result.execution_status) is not GuideRuntimeExecutionStatus)
        or (result.evidence_status is not None and type(result.evidence_status) is not GuideRuntimeEvidenceStatus)
        or type(result.release_decision) is not GuideRuntimeReleaseDecision
        or type(result.is_current) is not bool
        or (result.fallback_code is not None and type(result.fallback_code) is not GuidelineFallbackCode)
        or (
            result.authorized_selection is not None
            and type(result.authorized_selection) is not AuthorizedCitationSelection
        )
        or (result.card_outcome is not None and type(result.card_outcome) is not GuidelineCardOutcome)
    ):
        return False

    if result.release_decision is GuideRuntimeReleaseDecision.PASS:
        card_outcome = result.card_outcome
        return (
            result.execution_status is GuideRuntimeExecutionStatus.SUCCEEDED
            and result.evidence_status is GuideRuntimeEvidenceStatus.SUFFICIENT
            and result.is_current
            and result.fallback_code is None
            and type(result.authorized_selection) is AuthorizedCitationSelection
            and card_outcome is not None
            and card_outcome.status is GuidelineCardStatus.GENERATED
            and card_outcome.reason is GuidelineCardReason.CARD_GENERATED
            and card_outcome.card is not None
            and card_outcome.fallback_code is None
            and card_outcome.fallback is None
        )

    if result.card_outcome is None:
        return (
            result.execution_status is GuideRuntimeExecutionStatus.VALIDATION_ERROR
            and result.evidence_status is None
            and result.release_decision is GuideRuntimeReleaseDecision.REJECTED
            and result.is_current
            and result.fallback_code is None
            and result.authorized_selection is None
        )

    card_outcome = result.card_outcome
    code = card_outcome.fallback_code
    fallback = card_outcome.fallback
    if (
        type(code) is not GuidelineFallbackCode
        or type(fallback) is not VerifiedGuidelineFallback
        or fallback.code is not code
        or card_outcome.card is not None
        or result.fallback_code is not code
        or result.authorized_selection is not None
    ):
        return False
    projection = _FALLBACK_PROJECTIONS.get((card_outcome.status, card_outcome.reason, code))
    return projection is not None and (
        result.execution_status is projection.execution_status
        and result.evidence_status is projection.evidence_status
        and result.release_decision is projection.release_decision
        and result.is_current is projection.is_current
    )


def _fail_closed() -> GuideRuntimeReleaseResult:
    return GuideRuntimeReleaseResult(
        execution_status=GuideRuntimeExecutionStatus.VALIDATION_ERROR,
        evidence_status=None,
        release_decision=GuideRuntimeReleaseDecision.REJECTED,
        is_current=True,
        fallback_code=None,
        authorized_selection=None,
        card_outcome=None,
    )


def _is_completed_generation(value: object) -> bool:
    return (
        type(value) is GuideGenerationCardOutcome
        and value.decision is GuideGenerationCardDecision.COMPLETED
        and value.stopped_stage is None
        and type(value.card_outcome) is GuidelineCardOutcome
    )


def _project_approved_fallback(
    outcome: GuideCitationRuntimeOrchestrationOutcome,
) -> GuideRuntimeReleaseResult | None:
    if (
        outcome.decision is not GuideCitationRuntimeDecision.STOPPED
        or outcome.stopped_stage is not GuideCitationRuntimeStage.CLAIM_CITATION_VALIDATION
        or outcome.claim_citation_outcome is None
        or outcome.authorization_build_outcome is not None
        or outcome.authorization_request is not None
        or outcome.authority_outcome is not None
        or outcome.finalization_outcome is not None
        or not _is_completed_generation(outcome.generation_outcome)
    ):
        return None
    card_outcome = outcome.generation_outcome.card_outcome
    assert card_outcome is not None
    fallback = card_outcome.fallback
    code = card_outcome.fallback_code
    if (
        type(code) is not GuidelineFallbackCode
        or type(fallback) is not VerifiedGuidelineFallback
        or fallback.code is not code
        or card_outcome.card is not None
    ):
        return None
    projection = _FALLBACK_PROJECTIONS.get((card_outcome.status, card_outcome.reason, code))
    if projection is None:
        return None
    return GuideRuntimeReleaseResult(
        execution_status=projection.execution_status,
        evidence_status=projection.evidence_status,
        release_decision=projection.release_decision,
        is_current=projection.is_current,
        fallback_code=code,
        authorized_selection=None,
        card_outcome=card_outcome,
    )


def _project_authorized_success(
    outcome: GuideCitationRuntimeOrchestrationOutcome,
) -> GuideRuntimeReleaseResult | None:
    if (
        outcome.decision is not GuideCitationRuntimeDecision.COMPLETED
        or outcome.stopped_stage is not None
        or outcome.claim_citation_outcome is None
        or outcome.authorization_build_outcome is None
        or outcome.authorization_request is None
        or outcome.authority_outcome is None
        or type(outcome.finalization_outcome) is not AuthorizedCitationSelection
        or not _is_completed_generation(outcome.generation_outcome)
    ):
        return None
    card_outcome = outcome.generation_outcome.card_outcome
    assert card_outcome is not None
    if (
        card_outcome.status is not GuidelineCardStatus.GENERATED
        or card_outcome.reason is not GuidelineCardReason.CARD_GENERATED
        or card_outcome.card is None
        or card_outcome.fallback_code is not None
        or card_outcome.fallback is not None
    ):
        return None
    return GuideRuntimeReleaseResult(
        execution_status=GuideRuntimeExecutionStatus.SUCCEEDED,
        evidence_status=GuideRuntimeEvidenceStatus.SUFFICIENT,
        release_decision=GuideRuntimeReleaseDecision.PASS,
        is_current=True,
        fallback_code=None,
        authorized_selection=outcome.finalization_outcome,
        card_outcome=card_outcome,
    )


def finalize_guide_runtime_release(outcome: object) -> GuideRuntimeReleaseResult:
    """Project one authoritative orchestration outcome, failing closed on ambiguity."""

    if type(outcome) is not GuideCitationRuntimeOrchestrationOutcome:
        return _fail_closed()
    approved_fallback = _project_approved_fallback(outcome)
    if approved_fallback is not None:
        return approved_fallback
    authorized_success = _project_authorized_success(outcome)
    if authorized_success is not None:
        return authorized_success
    return _fail_closed()
