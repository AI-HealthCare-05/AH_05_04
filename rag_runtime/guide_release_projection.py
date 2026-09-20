"""Shared, versioned Guide runtime release output contract.

This module is the pure Worker-to-Backend boundary for approved Guide runtime
output. It owns no Guide evaluation, evidence, citation, or authorization
decision; the AI Worker adapter only projects already approved results into
these immutable, public-safe shapes.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

__all__ = [
    "GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION",
    "GuideRuntimeApprovedAnswer",
    "GuideRuntimeApprovedFallback",
    "GuideRuntimeFallbackCode",
    "GuideRuntimeReleaseDecision",
    "GuideRuntimeReleaseProjectionCarrier",
    "GuideRuntimeReleaseProjectionOutcome",
    "GuideRuntimeReleaseProjectionUnavailable",
    "GuideRuntimeVerifiedCitation",
]

GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION = "guide-runtime-release-projection-v1"


class GuideRuntimeReleaseDecision(StrEnum):
    PASS = "PASS"
    LIMITED = "LIMITED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class GuideRuntimeFallbackCode(StrEnum):
    NO_APPROVED_EVIDENCE = "NO_APPROVED_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PRESCRIPTION_STALE = "PRESCRIPTION_STALE"
    EXECUTION_CONTEXT_STALE = "EXECUTION_CONTEXT_STALE"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


@dataclass(frozen=True, slots=True)
class GuideRuntimeApprovedAnswer:
    """Lossless structural projection of text already approved in a Guide Card."""

    claim_action_texts: tuple[str, ...]
    uncertainty_text: str
    consultation_text: str

    def __post_init__(self) -> None:
        if (
            type(self.claim_action_texts) is not tuple
            or not self.claim_action_texts
            or not all(_is_nonblank_text(value) for value in self.claim_action_texts)
            or not _is_nonblank_text(self.uncertainty_text)
            or not _is_nonblank_text(self.consultation_text)
        ):
            raise ValueError("invalid approved Guide answer")


@dataclass(frozen=True, slots=True)
class GuideRuntimeApprovedFallback:
    """Existing approved fallback text with the fixed shared vocabulary."""

    code: GuideRuntimeFallbackCode
    text: str

    def __post_init__(self) -> None:
        if type(self.code) is not GuideRuntimeFallbackCode or not _is_nonblank_text(self.text):
            raise ValueError("invalid approved Guide fallback")


@dataclass(frozen=True, slots=True)
class GuideRuntimeVerifiedCitation:
    """Public-safe projection of an already validated and authorized citation."""

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

    def __post_init__(self) -> None:
        if (
            not all(
                _is_nonblank_text(value)
                for value in (
                    self.card_target_ref,
                    self.claim_key,
                    self.evidence_key,
                    self.source_type,
                    self.source_code,
                    self.source_version,
                    self.locator,
                    self.content_sha256,
                )
            )
            or type(self.source_snapshot_id) is not UUID
            or type(self.source_snapshot_member_id) is not UUID
            or type(self.display_order) is not int
            or self.display_order < 1
        ):
            raise ValueError("invalid verified Guide citation")


@dataclass(frozen=True, slots=True)
class GuideRuntimeReleaseProjectionCarrier:
    """Available, versioned output for a valid #893 Guide runtime release result."""

    contract_version: str
    release_decision: GuideRuntimeReleaseDecision
    is_current: bool
    answer: GuideRuntimeApprovedAnswer | None
    fallback: GuideRuntimeApprovedFallback | None
    citations: tuple[GuideRuntimeVerifiedCitation, ...]

    def __post_init__(self) -> None:
        if (
            self.contract_version != GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION
            or type(self.release_decision) is not GuideRuntimeReleaseDecision
            or type(self.is_current) is not bool
            or (self.answer is not None and type(self.answer) is not GuideRuntimeApprovedAnswer)
            or (self.fallback is not None and type(self.fallback) is not GuideRuntimeApprovedFallback)
            or type(self.citations) is not tuple
            or not all(type(citation) is GuideRuntimeVerifiedCitation for citation in self.citations)
        ):
            raise ValueError("invalid Guide release projection carrier")
        if self.release_decision is GuideRuntimeReleaseDecision.PASS:
            valid = self.is_current and self.answer is not None and self.fallback is None and bool(self.citations)
        else:
            valid = self.answer is None and self.fallback is not None and not self.citations
            if self.release_decision is GuideRuntimeReleaseDecision.STALE:
                valid = valid and not self.is_current
            else:
                valid = valid and self.is_current
        if not valid:
            raise ValueError("invalid Guide release projection variant")


@dataclass(frozen=True, slots=True)
class GuideRuntimeReleaseProjectionUnavailable:
    """Content-free fail-closed result for malformed or impossible runtime state."""

    contract_version: str = GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION:
            raise ValueError("invalid Guide release projection unavailable version")


type GuideRuntimeReleaseProjectionOutcome = (
    GuideRuntimeReleaseProjectionCarrier | GuideRuntimeReleaseProjectionUnavailable
)


def _is_nonblank_text(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip()
