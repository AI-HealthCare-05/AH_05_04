"""Narrow, fail-closed #180 binding from Hybrid retrieval to #697 input."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.evidence_search import ProductionSearchHit
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateReason,
    EvidenceGateStatus,
    EvidenceGateSuccess,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    HybridRetrieveOutcome,
    ProductionRetrievalOutcome,
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
)

__all__ = [
    "GuideRetrievalOutcomeBindingDecision",
    "GuideRetrievalOutcomeBindingOutcome",
    "GuideRetrievalOutcomeBindingReason",
    "project_hybrid_retrieval_for_guide_composition",
]


class GuideRetrievalOutcomeBindingDecision(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class GuideRetrievalOutcomeBindingReason(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    RETRIEVAL_NOT_SUCCEEDED = "RETRIEVAL_NOT_SUCCEEDED"
    SEARCH_RECEIPT_REQUIRED = "SEARCH_RECEIPT_REQUIRED"
    EVIDENCE_GATE_INVALID = "EVIDENCE_GATE_INVALID"


@dataclass(frozen=True, slots=True)
class GuideRetrievalOutcomeBindingOutcome:
    decision: GuideRetrievalOutcomeBindingDecision
    reason: GuideRetrievalOutcomeBindingReason | None
    retrieval_outcome: ProductionRetrievalOutcome | None = None


def _blocked(reason: GuideRetrievalOutcomeBindingReason) -> GuideRetrievalOutcomeBindingOutcome:
    return GuideRetrievalOutcomeBindingOutcome(
        decision=GuideRetrievalOutcomeBindingDecision.BLOCKED,
        reason=reason,
    )


def _is_complete_production_gate(value: object) -> bool:
    return (
        type(value) is EvidenceGateSuccess
        and value.status is EvidenceGateStatus.SUCCEEDED
        and value.reason is EvidenceGateReason.ELIGIBLE
        and type(value.selected_hits) is tuple
        and all(type(hit) is ProductionSearchHit for hit in value.selected_hits)
    )


def project_hybrid_retrieval_for_guide_composition(
    hybrid_outcome: object,
) -> GuideRetrievalOutcomeBindingOutcome:
    """Project only the semantic surface consumed by #697.

    This is neither a verifier nor a recovery path. It preserves the first-run
    search receipt and gate outcome by identity, does not produce a search
    success payload, and rejects replay-shaped outcomes that lack the original
    search receipt.
    """
    if type(hybrid_outcome) is not HybridRetrieveOutcome:
        return _blocked(GuideRetrievalOutcomeBindingReason.INVALID_INPUT)
    if hybrid_outcome.status is not RetrievalExecutionStatus.SUCCEEDED:
        return _blocked(GuideRetrievalOutcomeBindingReason.RETRIEVAL_NOT_SUCCEEDED)

    receipt = hybrid_outcome.search_receipt
    if (
        type(receipt) is not ProductionSearchReceipt
        or receipt.retrieval_execution_status is not RetrievalExecutionStatus.SUCCEEDED
    ):
        return _blocked(GuideRetrievalOutcomeBindingReason.SEARCH_RECEIPT_REQUIRED)
    if not _is_complete_production_gate(hybrid_outcome.gate_outcome):
        return _blocked(GuideRetrievalOutcomeBindingReason.EVIDENCE_GATE_INVALID)

    return GuideRetrievalOutcomeBindingOutcome(
        decision=GuideRetrievalOutcomeBindingDecision.READY,
        reason=None,
        retrieval_outcome=ProductionRetrievalOutcome(
            status=hybrid_outcome.status,
            receipt=receipt,
            gate_outcome=hybrid_outcome.gate_outcome,
            search_success=None,
            message="",
        ),
    )
