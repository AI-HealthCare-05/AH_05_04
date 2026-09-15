from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.evidence_search import ProductionSearchHit


class EvidenceGateStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    NO_RESULT = "NO_RESULT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class EvidenceGateReason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INSUFFICIENT = "INSUFFICIENT"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    INELIGIBLE = "INELIGIBLE"
    INVALID_BINDING = "INVALID_BINDING"


@dataclass(frozen=True, slots=True)
class EvidenceGateSuccess:
    selected_hits: tuple[ProductionSearchHit, ...]
    status: EvidenceGateStatus = EvidenceGateStatus.SUCCEEDED
    reason: EvidenceGateReason = EvidenceGateReason.ELIGIBLE


@dataclass(frozen=True, slots=True)
class EvidenceGateNoResult:
    message: str
    status: EvidenceGateStatus = EvidenceGateStatus.NO_RESULT
    reason: EvidenceGateReason = EvidenceGateReason.INSUFFICIENT


@dataclass(frozen=True, slots=True)
class EvidenceGateFailure:
    status: EvidenceGateStatus
    reason: EvidenceGateReason
    message: str


EvidenceGateOutcome = EvidenceGateSuccess | EvidenceGateNoResult | EvidenceGateFailure


@dataclass(frozen=True, slots=True)
class PreSearchEligibilityRequest:
    knowledge_index_id: UUID
    allowed_source_ids: tuple[UUID, ...] | None = None
    allowed_snapshot_ids: tuple[UUID, ...] | None = None


@dataclass(frozen=True, slots=True)
class PreSearchEligibilitySuccess:
    is_eligible: bool = True


@dataclass(frozen=True, slots=True)
class PreSearchEligibilityFailure:
    status: EvidenceGateStatus
    reason: EvidenceGateReason
    message: str


PreSearchEligibilityOutcome = PreSearchEligibilitySuccess | PreSearchEligibilityFailure


@dataclass(frozen=True, slots=True)
class PostSearchEligibilityRequest:
    knowledge_index_id: UUID


@dataclass(frozen=True, slots=True)
class PostSearchEligibilitySuccess:
    eligible_chunk_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class PostSearchEligibilityFailure:
    status: EvidenceGateStatus
    reason: EvidenceGateReason
    message: str


PostSearchEligibilityOutcome = PostSearchEligibilitySuccess | PostSearchEligibilityFailure


class ProductionEvidenceEligibilityVerifierPort(Protocol):
    async def pre_search(self, request: PreSearchEligibilityRequest) -> PreSearchEligibilityOutcome: ...

    async def post_search(
        self,
        request: PostSearchEligibilityRequest,
        hits: Sequence[ProductionSearchHit],
    ) -> PostSearchEligibilityOutcome: ...


def evaluate_evidence_gate(
    hits: Sequence[ProductionSearchHit],
    eligible_chunk_ids: frozenset[UUID],
) -> EvidenceGateOutcome:
    """Pure structural Evidence Gate evaluation.

    Rules:
    - Pre-gate Top 5 candidates only (fusion_rank <= 5).
    - Never promote rank 6+.
    - Only candidates whose knowledge_chunk_id is in eligible_chunk_ids.
    - Strictly preserve original fusion_rank ascending order (with gaps).
    - Deduplicate identical stable coordinates.
    - Zero eligible hits -> NO_RESULT/INSUFFICIENT.
    """
    if not hits:
        return EvidenceGateNoResult(
            status=EvidenceGateStatus.NO_RESULT,
            reason=EvidenceGateReason.INSUFFICIENT,
            message="No hits returned from retrieval",
        )

    # 1. Take only candidates with pre-gate fusion_rank <= 5
    pre_gate_candidates = [h for h in hits if h.fusion_rank <= 5]
    if not pre_gate_candidates:
        return EvidenceGateNoResult(
            status=EvidenceGateStatus.NO_RESULT,
            reason=EvidenceGateReason.INSUFFICIENT,
            message="No candidates with fusion_rank <= 5",
        )

    # 2. Filter by post-search eligibility
    eligible_candidates = [h for h in pre_gate_candidates if h.provenance.knowledge_chunk_id in eligible_chunk_ids]
    if not eligible_candidates:
        return EvidenceGateNoResult(
            status=EvidenceGateStatus.NO_RESULT,
            reason=EvidenceGateReason.INSUFFICIENT,
            message="No candidates passed post-search eligibility verification",
        )

    # 3. Deduplicate stable coordinates preserving original order
    seen_coords: set[tuple[str, str, str, int]] = set()
    selected: list[ProductionSearchHit] = []
    for h in eligible_candidates:
        coord_key = (
            h.coordinate.source_code,
            h.coordinate.source_version,
            h.coordinate.external_document_id,
            h.coordinate.chunk_index,
        )
        if coord_key in seen_coords:
            continue
        seen_coords.add(coord_key)
        selected.append(h)
        if len(selected) == 5:
            break

    return EvidenceGateSuccess(
        status=EvidenceGateStatus.SUCCEEDED,
        reason=EvidenceGateReason.ELIGIBLE,
        selected_hits=tuple(selected),
    )
