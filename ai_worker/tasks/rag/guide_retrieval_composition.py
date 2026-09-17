"""Guide Authority x Production Retrieval Exact Join (#697).

Pure composition slice joining the already-authenticated #672 authority bindings
with the already-succeeded #178 production retrieval selection.

Scope & Authority Boundaries:
- Pure/Read-Only Seam: no database access, no network I/O, no persistence.
- No New Authority: this module issues no decision of its own. It consumes the
  AUTHENTICATED outcome of `assemble_sync_guide_evidence_authority` and the
  SUCCEEDED outcome of `execute_production_retrieval` without re-deriving either
  meaning, and it owns no receipt, hash, or ranking policy.
- Production Boundary: only the production Evidence Gate
  (`production_evidence_gate.EvidenceGateSuccess`) and `ProductionSearchReceipt`
  are consumed. The provisional `evidence_gate` kernel and its
  `EvidenceGateRetrievalReceipt` are a separate receipt domain and are never
  converted, wrapped, or adapted here.
- Exact Join: bindings and selected hits are joined on exact equality of
  (source_snapshot_id, source_snapshot_member_id, source_code, source_version).
  No normalization, trimming, or case folding is applied.
- Fail-Closed Atomicity: composition is a bijection. Any missing, extra, or
  duplicate join key rejects the whole outcome; partial selections are never
  returned (`selections = ()`).
- No Inferred Pairing: rejection reasons report only what the exact join proves.
  A selected hit whose join key has no binding is BINDING_NOT_FOUND and an
  unconsumed binding is EXTRA_BINDING, even when both occur together. This seam
  owns no pairing or partial-key matching policy, so it never infers that an
  unmatched hit and an unconsumed binding were meant to be the same selection.
- Excluded Material: assessment authenticity, eligibility receipts, and chunk
  content hydration are not part of this seam. Downstream Guide Evidence Handoff
  requires that material and is deliberately not connected here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ai_worker.tasks.rag.evidence_search import ProductionSearchHit
from ai_worker.tasks.rag.guide_evidence_authority import (
    SyncGuideEvidenceAuthorityDecision,
    SyncGuideEvidenceAuthorityOutcome,
)
from ai_worker.tasks.rag.guide_evidence_handoff import RequestSourceMemberBinding
from ai_worker.tasks.rag.production_evidence_gate import EvidenceGateSuccess
from ai_worker.tasks.rag.retrieval_runtime import (
    ProductionRetrievalOutcome,
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
)

__all__ = [
    "AuthenticatedGuideRetrievalSelection",
    "GuideRetrievalCompositionDecision",
    "GuideRetrievalCompositionOutcome",
    "GuideRetrievalCompositionReason",
    "compose_guide_authority_with_production_retrieval",
]

GuideRetrievalJoinKey = tuple[UUID, UUID, str, str]


class GuideRetrievalCompositionDecision(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    REJECTED = "REJECTED"


class GuideRetrievalCompositionReason(StrEnum):
    AUTHORITY_NOT_AUTHENTICATED = "AUTHORITY_NOT_AUTHENTICATED"
    RETRIEVAL_NOT_SUCCEEDED = "RETRIEVAL_NOT_SUCCEEDED"
    RETRIEVAL_RECEIPT_MISSING = "RETRIEVAL_RECEIPT_MISSING"
    EVIDENCE_GATE_NOT_SUCCEEDED = "EVIDENCE_GATE_NOT_SUCCEEDED"
    BINDING_NOT_FOUND = "BINDING_NOT_FOUND"
    EXTRA_BINDING = "EXTRA_BINDING"
    DUPLICATE_BINDING = "DUPLICATE_BINDING"
    DUPLICATE_HIT = "DUPLICATE_HIT"


@dataclass(frozen=True, slots=True)
class AuthenticatedGuideRetrievalSelection:
    hit: ProductionSearchHit
    binding: RequestSourceMemberBinding


@dataclass(frozen=True, slots=True)
class GuideRetrievalCompositionOutcome:
    decision: GuideRetrievalCompositionDecision
    reasons: tuple[GuideRetrievalCompositionReason, ...]
    retrieval_receipt: ProductionSearchReceipt | None = None
    selections: tuple[AuthenticatedGuideRetrievalSelection, ...] = ()


def _rejected(reason: GuideRetrievalCompositionReason) -> GuideRetrievalCompositionOutcome:
    return GuideRetrievalCompositionOutcome(
        decision=GuideRetrievalCompositionDecision.REJECTED,
        reasons=(reason,),
        retrieval_receipt=None,
        selections=(),
    )


def _binding_join_key(binding: RequestSourceMemberBinding) -> GuideRetrievalJoinKey:
    return (
        binding.source_snapshot_id,
        binding.source_snapshot_member_id,
        binding.source_code,
        binding.source_version,
    )


def _hit_join_key(hit: ProductionSearchHit) -> GuideRetrievalJoinKey:
    return (
        hit.provenance.source_snapshot_id,
        hit.provenance.source_snapshot_member_id,
        hit.provenance.source_code,
        hit.provenance.source_version,
    )


def _verify_retrieval(
    retrieval_outcome: ProductionRetrievalOutcome,
) -> tuple[ProductionSearchReceipt | None, GuideRetrievalCompositionReason | None]:
    if type(retrieval_outcome) is not ProductionRetrievalOutcome:
        return None, GuideRetrievalCompositionReason.RETRIEVAL_NOT_SUCCEEDED
    if retrieval_outcome.status is not RetrievalExecutionStatus.SUCCEEDED:
        return None, GuideRetrievalCompositionReason.RETRIEVAL_NOT_SUCCEEDED

    receipt = retrieval_outcome.receipt
    if type(receipt) is not ProductionSearchReceipt:
        return None, GuideRetrievalCompositionReason.RETRIEVAL_RECEIPT_MISSING
    if receipt.retrieval_execution_status is not RetrievalExecutionStatus.SUCCEEDED:
        return None, GuideRetrievalCompositionReason.RETRIEVAL_NOT_SUCCEEDED

    if type(retrieval_outcome.gate_outcome) is not EvidenceGateSuccess:
        return None, GuideRetrievalCompositionReason.EVIDENCE_GATE_NOT_SUCCEEDED

    return receipt, None


def _index_bindings(
    bindings: tuple[RequestSourceMemberBinding, ...],
) -> tuple[dict[GuideRetrievalJoinKey, RequestSourceMemberBinding] | None, GuideRetrievalCompositionReason | None]:
    indexed: dict[GuideRetrievalJoinKey, RequestSourceMemberBinding] = {}
    for binding in bindings:
        key = _binding_join_key(binding)
        if key in indexed:
            return None, GuideRetrievalCompositionReason.DUPLICATE_BINDING
        indexed[key] = binding
    return indexed, None


def compose_guide_authority_with_production_retrieval(
    *,
    authority_outcome: SyncGuideEvidenceAuthorityOutcome,
    retrieval_outcome: ProductionRetrievalOutcome,
) -> GuideRetrievalCompositionOutcome:
    """Join AUTHENTICATED authority bindings with production selected hits fail-closed.

    Validation is phase-ordered and fail-fast:
    - Phase 1: Authority precondition (#672 AUTHENTICATED outcome only)
    - Phase 2: Production retrieval precondition (#178 SUCCEEDED outcome, receipt,
      and production Evidence Gate success)
    - Phase 3: Exact 1:1 join on the four authority coordinate fields

    On success, selections preserve the production `selected_hits` order; this
    module applies no ranking policy of its own.
    """
    # --------------------------------------------------------------------------
    # Phase 1: Authority Precondition
    # --------------------------------------------------------------------------
    if (
        type(authority_outcome) is not SyncGuideEvidenceAuthorityOutcome
        or authority_outcome.decision is not SyncGuideEvidenceAuthorityDecision.AUTHENTICATED
    ):
        return _rejected(GuideRetrievalCompositionReason.AUTHORITY_NOT_AUTHENTICATED)

    # --------------------------------------------------------------------------
    # Phase 2: Production Retrieval Precondition
    # --------------------------------------------------------------------------
    receipt, retrieval_err = _verify_retrieval(retrieval_outcome)
    if retrieval_err is not None:
        return _rejected(retrieval_err)
    assert receipt is not None

    gate_outcome = retrieval_outcome.gate_outcome
    assert isinstance(gate_outcome, EvidenceGateSuccess)
    selected_hits = gate_outcome.selected_hits

    # --------------------------------------------------------------------------
    # Phase 3: Exact 1:1 Join
    # --------------------------------------------------------------------------
    unmatched_bindings, duplicate_err = _index_bindings(authority_outcome.bindings)
    if duplicate_err is not None:
        return _rejected(duplicate_err)
    assert unmatched_bindings is not None

    selections: list[AuthenticatedGuideRetrievalSelection] = []
    seen_hit_keys: set[GuideRetrievalJoinKey] = set()

    for hit in selected_hits:
        key = _hit_join_key(hit)
        if key in seen_hit_keys:
            return _rejected(GuideRetrievalCompositionReason.DUPLICATE_HIT)
        seen_hit_keys.add(key)

        binding = unmatched_bindings.pop(key, None)
        if binding is None:
            return _rejected(GuideRetrievalCompositionReason.BINDING_NOT_FOUND)
        selections.append(AuthenticatedGuideRetrievalSelection(hit=hit, binding=binding))

    if unmatched_bindings:
        return _rejected(GuideRetrievalCompositionReason.EXTRA_BINDING)

    return GuideRetrievalCompositionOutcome(
        decision=GuideRetrievalCompositionDecision.AUTHENTICATED,
        reasons=(),
        retrieval_receipt=receipt,
        selections=tuple(selections),
    )
