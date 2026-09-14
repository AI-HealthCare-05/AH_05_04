from __future__ import annotations

from fractions import Fraction

import pytest

from ai_worker.tasks.rag.evidence_rank_fusion import (
    CandidateStageSignal,
    EvidenceFusionError,
    EvidenceFusionErrorCode,
    FractionReceipt,
    HybridFusionCandidate,
    LexicalCandidateInput,
    PureRrfCandidate,
    StableCoordinate,
    fuse_hybrid_rrf,
    fuse_lexical_subsearches,
    pure_rrf_rank_fusion,
    stable_coordinate_sort_key,
)


def _coord(
    source_code: str = "MFDS",
    source_version: str = "api:2026-09-08T00:00:00.000000Z:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    external_document_id: str = "doc-1",
    chunk_index: int = 0,
) -> StableCoordinate:
    return StableCoordinate(
        source_code=source_code,
        source_version=source_version,
        external_document_id=external_document_id,
        chunk_index=chunk_index,
    )


def test_stable_coordinate_sort_key_utf8() -> None:
    c1 = _coord(source_code="A", external_document_id="doc-1")
    c2 = _coord(source_code="B", external_document_id="doc-1")
    c_kr1 = _coord(source_code="가", external_document_id="doc-1")
    c_kr2 = _coord(source_code="나", external_document_id="doc-1")
    c_emoji = _coord(source_code="\U0001f600", external_document_id="doc-1")

    # ASCII < Hangul < Emoji in UTF-8
    assert stable_coordinate_sort_key(c1) < stable_coordinate_sort_key(c2)
    assert stable_coordinate_sort_key(c2) < stable_coordinate_sort_key(c_kr1)
    assert stable_coordinate_sort_key(c_kr1) < stable_coordinate_sort_key(c_kr2)
    assert stable_coordinate_sort_key(c_kr2) < stable_coordinate_sort_key(c_emoji)

    # chunk_index numeric order
    c_idx0 = _coord(chunk_index=0)
    c_idx10 = _coord(chunk_index=10)
    c_idx2 = _coord(chunk_index=2)
    assert stable_coordinate_sort_key(c_idx0) < stable_coordinate_sort_key(c_idx2)
    assert stable_coordinate_sort_key(c_idx2) < stable_coordinate_sort_key(c_idx10)


def test_pure_rrf_single_stage_golden_fractions() -> None:
    cand1 = PureRrfCandidate(
        coordinate=_coord(external_document_id="d1"),
        content_hash="a" * 64,
        stage_ranks={"LEXICAL": 1},
    )
    cand2 = PureRrfCandidate(
        coordinate=_coord(external_document_id="d2"),
        content_hash="b" * 64,
        stage_ranks={"LEXICAL": 2},
    )
    fused = pure_rrf_rank_fusion([cand1, cand2], rrf_k=60)
    assert len(fused) == 2
    assert fused[0].coordinate.external_document_id == "d1"
    assert fused[0].score == Fraction(1, 61)
    assert fused[0].fraction_receipt == FractionReceipt(numerator="1", denominator="61")
    assert fused[0].fusion_rank == 1

    assert fused[1].coordinate.external_document_id == "d2"
    assert fused[1].score == Fraction(1, 62)
    assert fused[1].fraction_receipt == FractionReceipt(numerator="1", denominator="62")
    assert fused[1].fusion_rank == 2


def test_pure_rrf_multi_stage_and_missing_stage() -> None:
    # cand1 has Lexical rank 1, Dense rank 2 => 1/61 + 1/62 = 123/3782
    cand1 = PureRrfCandidate(
        coordinate=_coord(external_document_id="d1"),
        content_hash="a" * 64,
        stage_ranks={"LEXICAL": 1, "DENSE": 2},
    )
    # cand2 has only Lexical rank 1 => 1/61
    cand2 = PureRrfCandidate(
        coordinate=_coord(external_document_id="d2"),
        content_hash="b" * 64,
        stage_ranks={"LEXICAL": 1},
    )
    # 123/3782 > 1/61 (approx 0.0325 > 0.01639)
    fused = pure_rrf_rank_fusion([cand2, cand1], rrf_k=60)
    assert fused[0].coordinate.external_document_id == "d1"
    assert fused[0].score == Fraction(123, 3782)
    assert fused[0].fraction_receipt == FractionReceipt(numerator="123", denominator="3782")
    assert fused[1].coordinate.external_document_id == "d2"
    assert fused[1].score == Fraction(1, 61)


def test_pure_rrf_exact_rational_tie_and_utf8_order() -> None:
    # Two candidates with identical score (1/61 + 1/62)
    # Tie broken by external_document_id UTF-8: "alpha" < "beta"
    cand_beta = PureRrfCandidate(
        coordinate=_coord(external_document_id="beta"),
        content_hash="b" * 64,
        stage_ranks={"LEXICAL": 1, "DENSE": 2},
    )
    cand_alpha = PureRrfCandidate(
        coordinate=_coord(external_document_id="alpha"),
        content_hash="a" * 64,
        stage_ranks={"LEXICAL": 1, "DENSE": 2},
    )
    fused = pure_rrf_rank_fusion([cand_beta, cand_alpha], rrf_k=60)
    assert fused[0].coordinate.external_document_id == "alpha"
    assert fused[1].coordinate.external_document_id == "beta"
    assert fused[0].score == fused[1].score


def test_pure_rrf_close_fraction_comparison_no_float_rounding() -> None:
    # Compare Fraction(1, 61) + Fraction(1, 600000000000000001) vs Fraction(1, 61)
    # High precision that would be lost in 64-bit float
    cand1 = PureRrfCandidate(
        coordinate=_coord(external_document_id="d1"),
        content_hash="a" * 64,
        stage_ranks={"STAGE1": 1, "STAGE2": 600000000000000001 - 60},
    )
    cand2 = PureRrfCandidate(
        coordinate=_coord(external_document_id="d2"),
        content_hash="b" * 64,
        stage_ranks={"STAGE1": 1},
    )
    fused = pure_rrf_rank_fusion([cand2, cand1], rrf_k=60)
    assert fused[0].coordinate.external_document_id == "d1"
    assert fused[1].coordinate.external_document_id == "d2"


def test_pure_rrf_duplicate_coordinate_content_mismatch_fails() -> None:
    coord = _coord(external_document_id="d1")
    cand1 = PureRrfCandidate(coordinate=coord, content_hash="a" * 64, stage_ranks={"S1": 1})
    cand2 = PureRrfCandidate(coordinate=coord, content_hash="b" * 64, stage_ranks={"S2": 1})
    with pytest.raises(EvidenceFusionError) as exc_info:
        pure_rrf_rank_fusion([cand1, cand2], rrf_k=60)
    assert exc_info.value.code == EvidenceFusionErrorCode.CONTENT_HASH_MISMATCH


def test_pure_rrf_invalid_rank_fails() -> None:
    # 0 or negative rank
    cand = PureRrfCandidate(
        coordinate=_coord(),
        content_hash="a" * 64,
        stage_ranks={"S1": 0},
    )
    with pytest.raises(EvidenceFusionError) as exc_info:
        pure_rrf_rank_fusion([cand], rrf_k=60)
    assert exc_info.value.code == EvidenceFusionErrorCode.INVALID_RANK


def test_lexical_subsearch_fusion_exact_priority() -> None:
    # Candidate 1: Non-exact hit, high Trigram score
    c_non_exact = LexicalCandidateInput(
        coordinate=_coord(external_document_id="doc-ne"),
        content_hash="a" * 64,
        is_exact=False,
        trigram_score=0.95,
        fts_score=0.95,
    )
    # Candidate 2: Exact hit, low Trigram score
    c_exact = LexicalCandidateInput(
        coordinate=_coord(external_document_id="doc-ex"),
        content_hash="b" * 64,
        is_exact=True,
        trigram_score=0.35,
        fts_score=0.1,
    )
    # Exact bucket must strictly precede non-exact bucket!
    result = fuse_lexical_subsearches([c_non_exact, c_exact], limit=20, rrf_k=60)
    assert len(result) == 2
    assert result[0].coordinate.external_document_id == "doc-ex"
    assert result[0].is_exact is True
    assert result[0].lexical_rank == 1
    assert result[1].coordinate.external_document_id == "doc-ne"
    assert result[1].is_exact is False
    assert result[1].lexical_rank == 2


def test_hybrid_fusion_top30_and_future_reranker_limit() -> None:
    candidates: list[HybridFusionCandidate] = []
    for i in range(1, 36):
        candidates.append(
            HybridFusionCandidate(
                coordinate=_coord(external_document_id=f"doc-{i:02d}"),
                content_hash="c" * 64,
                lexical_signal=CandidateStageSignal(rank=i, score_decimal="1") if i <= 20 else None,
                dense_signal=CandidateStageSignal(rank=i, score_decimal="0.9") if i <= 20 else None,
            )
        )
    result = fuse_hybrid_rrf(candidates, rrf_k=60, hybrid_limit=30, reranker_input_limit=20)
    assert len(result) == 30
    for idx, item in enumerate(result):
        assert item.fusion_rank == idx + 1
        if idx < 20:
            assert item.is_eligible_for_future_reranker is True
        else:
            assert item.is_eligible_for_future_reranker is False
