"""P0-B canonical carrier for multiple already-verified Guide handoffs.

This module owns only the cross-run boundary.  It never constructs a retrieval
receipt or handoff, re-ranks evidence, or changes a child's #760 verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from ai_worker.tasks.rag.guide_evidence_handoff import VerifiedGuideEvidenceHandoff
from ai_worker.tasks.rag.guideline_production_evidence import (
    ProductionGuidelineEvidenceSet,
    project_guideline_evidence_from_handoff,
)

if TYPE_CHECKING:
    from ai_worker.tasks.rag.guideline_card import MedicationIdentityRef

__all__ = [
    "GuideAggregateEvidence",
    "GuideAggregateEvidenceAssemblyDecision",
    "GuideAggregateEvidenceAssemblyOutcome",
    "GuideAggregateEvidenceEntry",
    "GuideAggregateEvidenceRun",
    "assemble_guide_aggregate_evidence",
    "iter_guide_aggregate_selections",
    "project_guideline_evidence_from_aggregate",
]


class GuideAggregateEvidenceAssemblyDecision(StrEnum):
    ASSEMBLED = "ASSEMBLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class GuideAggregateEvidenceEntry:
    """One caller-ordered medication/run and its already-verified #760 handoff."""

    medication_identity: MedicationIdentityRef
    retrieval_run_id: UUID
    handoff: VerifiedGuideEvidenceHandoff


@dataclass(frozen=True, slots=True)
class GuideAggregateEvidenceRun:
    """A child boundary preserved with its existing #774 projection."""

    medication_identity: MedicationIdentityRef
    retrieval_run_id: UUID
    handoff: VerifiedGuideEvidenceHandoff
    evidence: ProductionGuidelineEvidenceSet


@dataclass(frozen=True, slots=True)
class GuideAggregateEvidence:
    """Ordered child runs; this is deliberately not a synthetic evidence set."""

    entries: tuple[GuideAggregateEvidenceRun, ...]


@dataclass(frozen=True, slots=True)
class GuideAggregateEvidenceAssemblyOutcome:
    decision: GuideAggregateEvidenceAssemblyDecision
    aggregate: GuideAggregateEvidence | None


def _is_valid_medication_identity(value: object) -> bool:
    # Import lazily: ``guideline_card`` itself consumes production evidence.
    from ai_worker.tasks.rag.guideline_card import MedicationIdentityRef

    return type(value) is MedicationIdentityRef and all(
        isinstance(field, str) and field.strip()
        for field in (
            value.prescription_version_medication_id,
            value.code_system,
            value.canonical_code,
        )
    )


def _is_valid_entry(value: object) -> bool:
    return (
        type(value) is GuideAggregateEvidenceEntry
        and _is_valid_medication_identity(value.medication_identity)
        and type(value.retrieval_run_id) is UUID
        and type(value.handoff) is VerifiedGuideEvidenceHandoff
        and type(value.handoff.selections) is tuple
        and bool(value.handoff.selections)
    )


def assemble_guide_aggregate_evidence(
    entries: tuple[GuideAggregateEvidenceEntry, ...],
) -> GuideAggregateEvidenceAssemblyOutcome:
    """Preserve caller order while enforcing only new cross-child invariants.

    An evidence key is anchored at the persisted database uniqueness coordinate
    ``(source_snapshot_id, evidence_key)``. Repeating that anchor is valid when its
    immutable Source/member/content identity agrees; each run membership remains a
    separate entry. A disagreement fails closed before generation can be invoked.
    """
    if type(entries) is not tuple or not entries or not all(_is_valid_entry(item) for item in entries):
        return GuideAggregateEvidenceAssemblyOutcome(GuideAggregateEvidenceAssemblyDecision.REJECTED, None)

    bindings: set[tuple[object, UUID]] = set()
    immutable_by_anchor: dict[tuple[object, str], tuple[object, str, str, str, str]] = {}
    runs: list[GuideAggregateEvidenceRun] = []
    for entry in entries:
        binding = (entry.medication_identity, entry.retrieval_run_id)
        if binding in bindings:
            return GuideAggregateEvidenceAssemblyOutcome(GuideAggregateEvidenceAssemblyDecision.REJECTED, None)
        bindings.add(binding)
        for selection in entry.handoff.selections:
            anchor = (selection.source_snapshot_id, selection.evidence_key)
            immutable_identity = (
                selection.source_snapshot_member_id,
                selection.source_code,
                selection.source_version,
                selection.locator,
                selection.content_sha256,
            )
            previous = immutable_by_anchor.setdefault(anchor, immutable_identity)
            if previous != immutable_identity:
                return GuideAggregateEvidenceAssemblyOutcome(GuideAggregateEvidenceAssemblyDecision.REJECTED, None)
        runs.append(
            GuideAggregateEvidenceRun(
                medication_identity=entry.medication_identity,
                retrieval_run_id=entry.retrieval_run_id,
                handoff=entry.handoff,
                evidence=project_guideline_evidence_from_handoff(entry.handoff),
            )
        )
    return GuideAggregateEvidenceAssemblyOutcome(
        GuideAggregateEvidenceAssemblyDecision.ASSEMBLED,
        GuideAggregateEvidence(entries=tuple(runs)),
    )


def project_guideline_evidence_from_aggregate(aggregate: GuideAggregateEvidence) -> GuideAggregateEvidence:
    """Expose the aggregate as the generator input without flattening its children."""
    if type(aggregate) is not GuideAggregateEvidence or not aggregate.entries:
        raise ValueError("aggregate must contain at least one verified child handoff")
    return aggregate


def iter_guide_aggregate_selections(aggregate: GuideAggregateEvidence):
    """Yield a read-only provider projection in structural run order.

    This is not a carrier transformation: entries, child receipts and child handoffs
    remain nested in ``aggregate``. The iterator only supplies the existing generator
    with the selections it must present to its provider.
    """
    for entry in aggregate.entries:
        yield from entry.evidence.selections
