"""Backend projection boundary for approved Guide runtime release output."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rag_runtime.guide_release_projection import (
    GuideRuntimeApprovedAnswer,
    GuideRuntimeApprovedFallback,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeReleaseProjectionOutcome,
    GuideRuntimeReleaseProjectionUnavailable,
    GuideRuntimeVerifiedCitation,
)


class GuideRuntimeProjectionKind(StrEnum):
    ANSWER = "ANSWER"
    LIMITED_FALLBACK = "LIMITED_FALLBACK"
    REJECTED_FALLBACK = "REJECTED_FALLBACK"
    STALE_FALLBACK = "STALE_FALLBACK"
    FAIL_CLOSED_REJECTION = "FAIL_CLOSED_REJECTION"


class GuideRuntimePersistenceGap(StrEnum):
    RELEASE_STATUS_NOT_PERSISTED = "RELEASE_STATUS_NOT_PERSISTED"
    FALLBACK_NOT_PERSISTED = "FALLBACK_NOT_PERSISTED"
    LEGACY_CITATION_TABLE_INCOMPATIBLE = "LEGACY_CITATION_TABLE_INCOMPATIBLE"
    NO_PUBLIC_CONTENT = "NO_PUBLIC_CONTENT"


@dataclass(frozen=True, slots=True)
class GuideRuntimePublicProjectionCandidate:
    kind: GuideRuntimeProjectionKind
    is_current: bool | None
    answer: GuideRuntimeApprovedAnswer | None
    fallback: GuideRuntimeApprovedFallback | None
    citations: tuple[GuideRuntimeVerifiedCitation, ...]
    persistence_gaps: tuple[GuideRuntimePersistenceGap, ...]


def project_guide_runtime_release(
    outcome: GuideRuntimeReleaseProjectionOutcome,
) -> GuideRuntimePublicProjectionCandidate:
    """Project only fields approved by the canonical shared carrier."""

    if type(outcome) is GuideRuntimeReleaseProjectionUnavailable:
        return _fail_closed()
    if type(outcome) is not GuideRuntimeReleaseProjectionCarrier:
        return _fail_closed()
    if outcome.release_decision is GuideRuntimeReleaseDecision.PASS:
        return _project_answer(outcome)
    return _project_fallback(outcome)


def _project_answer(carrier: GuideRuntimeReleaseProjectionCarrier) -> GuideRuntimePublicProjectionCandidate:
    if carrier.answer is None:
        return _fail_closed()
    gaps = [GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED]
    if carrier.citations:
        gaps.append(GuideRuntimePersistenceGap.LEGACY_CITATION_TABLE_INCOMPATIBLE)
    return GuideRuntimePublicProjectionCandidate(
        kind=GuideRuntimeProjectionKind.ANSWER,
        is_current=True,
        answer=carrier.answer,
        fallback=None,
        citations=carrier.citations,
        persistence_gaps=tuple(gaps),
    )


def _project_fallback(carrier: GuideRuntimeReleaseProjectionCarrier) -> GuideRuntimePublicProjectionCandidate:
    if carrier.fallback is None:
        return _fail_closed()
    kind_by_decision = {
        GuideRuntimeReleaseDecision.LIMITED: GuideRuntimeProjectionKind.LIMITED_FALLBACK,
        GuideRuntimeReleaseDecision.REJECTED: GuideRuntimeProjectionKind.REJECTED_FALLBACK,
        GuideRuntimeReleaseDecision.STALE: GuideRuntimeProjectionKind.STALE_FALLBACK,
    }
    kind = kind_by_decision.get(carrier.release_decision)
    if kind is None:
        return _fail_closed()
    return GuideRuntimePublicProjectionCandidate(
        kind=kind,
        is_current=carrier.is_current,
        answer=None,
        fallback=carrier.fallback,
        citations=(),
        persistence_gaps=(
            GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED,
            GuideRuntimePersistenceGap.FALLBACK_NOT_PERSISTED,
        ),
    )


def _fail_closed() -> GuideRuntimePublicProjectionCandidate:
    return GuideRuntimePublicProjectionCandidate(
        kind=GuideRuntimeProjectionKind.FAIL_CLOSED_REJECTION,
        is_current=None,
        answer=None,
        fallback=None,
        citations=(),
        persistence_gaps=(GuideRuntimePersistenceGap.NO_PUBLIC_CONTENT,),
    )
