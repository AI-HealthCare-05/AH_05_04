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
- Cardinality (N chunk hits : 1 member binding): the authority join key is a
  Source Member unit while `selected_hits` is a chunk unit, so several distinct
  chunks of one Source Member are a legitimate production result. Each selected
  hit must resolve exactly one authenticated binding, and one binding may be
  reused by multiple distinct chunk hits. This is not a global bijection.
- Duplicate Identity: hit duplication is judged on the production Evidence
  Gate's own stable coordinate (source_code, source_version,
  external_document_id, chunk_index), reusing upstream semantics rather than
  defining a new dedupe policy here. Repeated member authority keys across
  distinct coordinates are not duplicates.
- Fail-Closed Atomicity: validation is phase-ordered and fail-fast, and a
  rejection returns a single typed reason with no partial selections
  (`selections = ()`). A selected hit with no binding for its authority key is
  BINDING_NOT_FOUND immediately; EXTRA_BINDING is reported only after every hit
  has joined and some supplied binding was still never used. Failed join keys
  are deliberately not surfaced on the outcome: this seam is not an operational
  diagnostic schema.
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
GuideRetrievalHitIdentity = tuple[str, str, str, int]


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


def _hit_identity(hit: ProductionSearchHit) -> GuideRetrievalHitIdentity:
    """Return the production Evidence Gate stable coordinate identity of a hit."""
    return (
        hit.coordinate.source_code,
        hit.coordinate.source_version,
        hit.coordinate.external_document_id,
        hit.coordinate.chunk_index,
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
    """Index authenticated bindings by authority key, rejecting a repeated member key."""
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

    Validation is phase-ordered and fail-fast, and a rejection returns exactly one
    typed reason:
    - Phase 1: Authority precondition (#672 AUTHENTICATED outcome only)
    - Phase 2: Production retrieval precondition (#178 SUCCEEDED outcome, receipt,
      and production Evidence Gate success)
    - Phase 3: Exact join on the four authority coordinate fields

    Success requires that every selected hit resolves exactly one authenticated
    member binding, that every supplied binding is used by at least one selected
    hit, that no authority member key carries more than one binding, and that no
    two selected hits share a stable coordinate. One binding may legitimately be
    reused by several distinct chunk hits of the same Source Member.

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
    # Phase 3: Exact Join (N chunk hits : 1 member binding)
    # --------------------------------------------------------------------------
    bindings_by_key, duplicate_err = _index_bindings(authority_outcome.bindings)
    if duplicate_err is not None:
        return _rejected(duplicate_err)
    assert bindings_by_key is not None

    selections: list[AuthenticatedGuideRetrievalSelection] = []
    seen_hit_identities: set[GuideRetrievalHitIdentity] = set()
    used_binding_keys: set[GuideRetrievalJoinKey] = set()

    for hit in selected_hits:
        identity = _hit_identity(hit)
        if identity in seen_hit_identities:
            return _rejected(GuideRetrievalCompositionReason.DUPLICATE_HIT)
        seen_hit_identities.add(identity)

        key = _hit_join_key(hit)
        binding = bindings_by_key.get(key)
        if binding is None:
            return _rejected(GuideRetrievalCompositionReason.BINDING_NOT_FOUND)
        used_binding_keys.add(key)
        selections.append(AuthenticatedGuideRetrievalSelection(hit=hit, binding=binding))

    if set(bindings_by_key) - used_binding_keys:
        return _rejected(GuideRetrievalCompositionReason.EXTRA_BINDING)

    return GuideRetrievalCompositionOutcome(
        decision=GuideRetrievalCompositionDecision.AUTHENTICATED,
        reasons=(),
        retrieval_receipt=receipt,
        selections=tuple(selections),
    )
