"""Backend-side projection boundary for #180 Guide runtime release results.

Backend consumes only the versioned internal carrier that #180's canonical
runtime projection adapter will produce. It does not import or introspect
``ai_worker`` runtime objects, which keeps the PD-175 import boundary closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

GUIDE_RUNTIME_PROJECTION_CARRIER_VERSION = "guide-runtime-public-projection-carrier-v1"


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
class GuideRuntimeCanonicalCitationIdentity:
    card_target_ref: str
    claim_key: str
    evidence_key: str
    source_type: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    locator: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class GuideRuntimeCitationProjectionCarrier:
    card_identity: GuideRuntimeCanonicalCitationIdentity
    authorized_identity: GuideRuntimeCanonicalCitationIdentity


@dataclass(frozen=True, slots=True)
class GuideRuntimeReleaseProjectionCarrier:
    contract_version: str
    release_decision: str
    is_current: bool
    answer_text: str | None
    fallback_text: str | None
    citations: tuple[GuideRuntimeCitationProjectionCarrier, ...] = ()


@dataclass(frozen=True, slots=True)
class GuideRuntimeCitationProjectionCandidate:
    card_target_ref: str
    claim_key: str
    evidence_key: str
    source_type: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    locator: str
    content_sha256: str
    display_order: int
    legacy_guide_citation_supported: bool
    legacy_guide_citation_blocker: str | None


@dataclass(frozen=True, slots=True)
class GuideRuntimePublicProjectionCandidate:
    kind: GuideRuntimeProjectionKind
    is_current: bool
    answer_text: str | None
    fallback_text: str | None
    citations: tuple[GuideRuntimeCitationProjectionCandidate, ...]
    persistence_gaps: tuple[GuideRuntimePersistenceGap, ...]


def project_guide_runtime_release(
    carrier: GuideRuntimeReleaseProjectionCarrier,
) -> GuideRuntimePublicProjectionCandidate:
    """Project only response-safe candidates from a canonical #180 carrier."""

    if carrier.contract_version != GUIDE_RUNTIME_PROJECTION_CARRIER_VERSION:
        return _fail_closed(carrier)
    if carrier.release_decision == "PASS":
        return _project_answer(carrier)
    if carrier.release_decision == "LIMITED":
        return _project_fallback(carrier, GuideRuntimeProjectionKind.LIMITED_FALLBACK)
    if carrier.release_decision == "STALE":
        return _project_fallback(carrier, GuideRuntimeProjectionKind.STALE_FALLBACK)
    if carrier.fallback_text is None:
        return _fail_closed(carrier)
    return _project_fallback(carrier, GuideRuntimeProjectionKind.REJECTED_FALLBACK)


def _project_answer(carrier: GuideRuntimeReleaseProjectionCarrier) -> GuideRuntimePublicProjectionCandidate:
    if not _has_public_text(carrier.answer_text) or not carrier.citations:
        return _fail_closed(carrier)

    projected: list[GuideRuntimeCitationProjectionCandidate] = []
    for display_order, citation in enumerate(carrier.citations, start=1):
        if citation.card_identity != citation.authorized_identity:
            return _fail_closed(carrier)
        projected.append(_project_citation(citation.card_identity, display_order=display_order))

    gaps: list[GuideRuntimePersistenceGap] = [GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED]
    if any(not citation.legacy_guide_citation_supported for citation in projected):
        gaps.append(GuideRuntimePersistenceGap.LEGACY_CITATION_TABLE_INCOMPATIBLE)
    return GuideRuntimePublicProjectionCandidate(
        kind=GuideRuntimeProjectionKind.ANSWER,
        is_current=True,
        answer_text=carrier.answer_text,
        fallback_text=None,
        citations=tuple(projected),
        persistence_gaps=tuple(gaps),
    )


def _project_fallback(
    carrier: GuideRuntimeReleaseProjectionCarrier,
    kind: GuideRuntimeProjectionKind,
) -> GuideRuntimePublicProjectionCandidate:
    if not _has_public_text(carrier.fallback_text):
        return _fail_closed(carrier)
    return GuideRuntimePublicProjectionCandidate(
        kind=kind,
        is_current=carrier.is_current,
        answer_text=None,
        fallback_text=carrier.fallback_text,
        citations=(),
        persistence_gaps=(
            GuideRuntimePersistenceGap.RELEASE_STATUS_NOT_PERSISTED,
            GuideRuntimePersistenceGap.FALLBACK_NOT_PERSISTED,
        ),
    )


def _fail_closed(carrier: GuideRuntimeReleaseProjectionCarrier) -> GuideRuntimePublicProjectionCandidate:
    return GuideRuntimePublicProjectionCandidate(
        kind=GuideRuntimeProjectionKind.FAIL_CLOSED_REJECTION,
        is_current=carrier.is_current,
        answer_text=None,
        fallback_text=None,
        citations=(),
        persistence_gaps=(GuideRuntimePersistenceGap.NO_PUBLIC_CONTENT,),
    )


def _project_citation(
    identity: GuideRuntimeCanonicalCitationIdentity,
    *,
    display_order: int,
) -> GuideRuntimeCitationProjectionCandidate:
    legacy_supported, blocker = _legacy_guide_citation_storage_scope(identity.source_type)
    return GuideRuntimeCitationProjectionCandidate(
        card_target_ref=identity.card_target_ref,
        claim_key=identity.claim_key,
        evidence_key=identity.evidence_key,
        source_type=identity.source_type,
        source_snapshot_id=identity.source_snapshot_id,
        source_snapshot_member_id=identity.source_snapshot_member_id,
        source_code=identity.source_code,
        source_version=identity.source_version,
        locator=identity.locator,
        content_sha256=identity.content_sha256,
        display_order=display_order,
        legacy_guide_citation_supported=legacy_supported,
        legacy_guide_citation_blocker=blocker,
    )


def _legacy_guide_citation_storage_scope(source_type: str) -> tuple[bool, str | None]:
    if source_type == "LIFESTYLE_GUIDELINE":
        return (
            False,
            "guide_citation requires knowledge_chunk_id/claim_text/cited_text and cannot store runtime source coordinates",
        )
    return False, "runtime citation source type has no legacy guide_citation mapping"


def _has_public_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())
