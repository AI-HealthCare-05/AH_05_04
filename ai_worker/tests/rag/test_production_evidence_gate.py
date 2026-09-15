from __future__ import annotations

from uuid import UUID, uuid4

from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt, StableCoordinate
from ai_worker.tasks.rag.evidence_search import (
    ProductionEvidenceProvenance,
    ProductionSearchHit,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateNoResult,
    EvidenceGateReason,
    EvidenceGateStatus,
    EvidenceGateSuccess,
    evaluate_evidence_gate,
)


def _make_hit(
    rank: int,
    chunk_id: UUID | None = None,
    coordinate: StableCoordinate | None = None,
) -> ProductionSearchHit:
    cid = chunk_id or uuid4()
    coord = coordinate or StableCoordinate(
        source_code="TEST_SRC",
        source_version="1.0",
        external_document_id=f"doc-{rank}",
        chunk_index=rank,
    )
    prov = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="TEST_IDX",
        index_version="1.0",
        index_configuration_hash="a" * 64,
        knowledge_chunk_id=cid,
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=uuid4(),
        source_code=coord.source_code,
        source_version=coord.source_version,
        canonical_checksum="b" * 64,
        external_document_id=coord.external_document_id,
        chunk_index=coord.chunk_index,
        locator=f"loc-{rank}",
        content_hash="c" * 64,
        canonicalization_spec_version="v1",
        normalization_version="v1",
    )
    return ProductionSearchHit(
        provenance=prov,
        coordinate=coord,
        exact_hit=True,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=rank,
        dense_rank=rank,
        fusion_rank=rank,
        fraction_receipt=FractionReceipt("1", "61"),
        is_eligible_for_future_reranker=True,
    )


def test_gate_accepts_pre_gate_top_5_subset_in_strict_ascending_order() -> None:
    hit1 = _make_hit(1)
    hit2 = _make_hit(2)
    hit3 = _make_hit(3)
    hit4 = _make_hit(4)
    hit5 = _make_hit(5)
    hit6 = _make_hit(6)

    # hits 1, 2, 4 are eligible, 3 is ineligible, 6 is eligible but rank 6
    eligible = frozenset(
        [
            hit1.provenance.knowledge_chunk_id,
            hit2.provenance.knowledge_chunk_id,
            hit4.provenance.knowledge_chunk_id,
            hit6.provenance.knowledge_chunk_id,
        ]
    )

    outcome = evaluate_evidence_gate([hit1, hit2, hit3, hit4, hit5, hit6], eligible)
    assert isinstance(outcome, EvidenceGateSuccess)
    assert outcome.status == EvidenceGateStatus.SUCCEEDED
    assert outcome.reason == EvidenceGateReason.ELIGIBLE
    assert len(outcome.selected_hits) == 3
    assert [h.fusion_rank for h in outcome.selected_hits] == [1, 2, 4]


def test_gate_never_promotes_rank_6_plus() -> None:
    hit1 = _make_hit(1)
    hit6 = _make_hit(6)

    # Only rank 6 is eligible; rank 1 is not
    eligible = frozenset([hit6.provenance.knowledge_chunk_id])

    outcome = evaluate_evidence_gate([hit1, hit6], eligible)
    assert isinstance(outcome, EvidenceGateNoResult)
    assert outcome.status == EvidenceGateStatus.NO_RESULT
    assert outcome.reason == EvidenceGateReason.INSUFFICIENT


def test_gate_rejects_duplicate_stable_coordinates() -> None:
    coord = StableCoordinate(
        source_code="TEST_SRC",
        source_version="1.0",
        external_document_id="doc-dup",
        chunk_index=0,
    )
    hit1 = _make_hit(1, coordinate=coord)
    hit2 = _make_hit(2, coordinate=coord)  # same coordinate, duplicate

    eligible = frozenset(
        [
            hit1.provenance.knowledge_chunk_id,
            hit2.provenance.knowledge_chunk_id,
        ]
    )

    outcome = evaluate_evidence_gate([hit1, hit2], eligible)
    assert isinstance(outcome, EvidenceGateSuccess)
    # Duplicate coordinate rejected, exactly 1 selected
    assert len(outcome.selected_hits) == 1
    assert outcome.selected_hits[0].fusion_rank == 1


def test_gate_zero_hits_maps_to_no_result_insufficient() -> None:
    outcome = evaluate_evidence_gate([], frozenset())
    assert isinstance(outcome, EvidenceGateNoResult)
    assert outcome.status == EvidenceGateStatus.NO_RESULT
    assert outcome.reason == EvidenceGateReason.INSUFFICIENT


def test_gate_all_pre_gate_hits_ineligible_maps_to_insufficient() -> None:
    hit1 = _make_hit(1)
    hit2 = _make_hit(2)

    # None eligible
    outcome = evaluate_evidence_gate([hit1, hit2], frozenset())
    assert isinstance(outcome, EvidenceGateNoResult)
    assert outcome.status == EvidenceGateStatus.NO_RESULT
    assert outcome.reason == EvidenceGateReason.INSUFFICIENT
