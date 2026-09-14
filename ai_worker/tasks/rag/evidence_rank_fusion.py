"""Pure implementation of rrf-rank-fusion@1 and deterministic candidate ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

RRF_ALGORITHM_ID = "rrf-rank-fusion@1"


class EvidenceFusionErrorCode(StrEnum):
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    INVALID_RANK = "INVALID_RANK"
    DUPLICATE_STAGE_RANK = "DUPLICATE_STAGE_RANK"
    EMPTY_INPUT = "EMPTY_INPUT"


class EvidenceFusionError(Exception):
    def __init__(self, code: EvidenceFusionErrorCode, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class StableCoordinate:
    source_code: str
    source_version: str
    external_document_id: str
    chunk_index: int


def stable_coordinate_sort_key(c: StableCoordinate) -> tuple[bytes, bytes, bytes, int]:
    return (
        c.source_code.encode("utf-8"),
        c.source_version.encode("utf-8"),
        c.external_document_id.encode("utf-8"),
        c.chunk_index,
    )


@dataclass(frozen=True, slots=True)
class FractionReceipt:
    numerator: str
    denominator: str


def make_fraction_receipt(frac: Fraction) -> FractionReceipt:
    return FractionReceipt(
        numerator=str(frac.numerator),
        denominator=str(frac.denominator),
    )


@dataclass(frozen=True, slots=True)
class PureRrfCandidate:
    coordinate: StableCoordinate
    content_hash: str
    stage_ranks: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class PureRrfResult:
    coordinate: StableCoordinate
    content_hash: str
    score: Fraction
    fraction_receipt: FractionReceipt
    fusion_rank: int


def _merge_candidate_into_map(
    cand: PureRrfCandidate,
    coord_map: dict[StableCoordinate, PureRrfCandidate],
) -> None:
    for stage, rank in cand.stage_ranks.items():
        if rank <= 0 or isinstance(rank, bool):
            raise EvidenceFusionError(
                EvidenceFusionErrorCode.INVALID_RANK,
                f"Invalid rank {rank} in stage {stage}",
            )
    if cand.coordinate not in coord_map:
        coord_map[cand.coordinate] = cand
        return

    prev = coord_map[cand.coordinate]
    if prev.content_hash != cand.content_hash:
        raise EvidenceFusionError(
            EvidenceFusionErrorCode.CONTENT_HASH_MISMATCH,
            f"Content hash mismatch for coordinate {cand.coordinate}",
        )
    merged_ranks = dict(prev.stage_ranks)
    for stage, rank in cand.stage_ranks.items():
        if stage in merged_ranks:
            raise EvidenceFusionError(
                EvidenceFusionErrorCode.DUPLICATE_STAGE_RANK,
                f"Duplicate rank for stage {stage}",
            )
        merged_ranks[stage] = rank
    coord_map[cand.coordinate] = PureRrfCandidate(
        coordinate=cand.coordinate,
        content_hash=cand.content_hash,
        stage_ranks=merged_ranks,
    )


def pure_rrf_rank_fusion(
    candidates: Sequence[PureRrfCandidate],
    *,
    rrf_k: int = 60,
) -> list[PureRrfResult]:
    if rrf_k <= 0:
        raise EvidenceFusionError(EvidenceFusionErrorCode.INVALID_RANK, "rrf_k must be positive")

    coord_map: dict[StableCoordinate, PureRrfCandidate] = {}
    for cand in candidates:
        _merge_candidate_into_map(cand, coord_map)

    scored: list[tuple[Fraction, PureRrfCandidate]] = []
    for cand in coord_map.values():
        total_score = sum((Fraction(1, rrf_k + rank) for rank in cand.stage_ranks.values()), Fraction(0, 1))
        scored.append((total_score, cand))

    scored.sort(
        key=lambda item: (-item[0], stable_coordinate_sort_key(item[1].coordinate)),
    )

    return [
        PureRrfResult(
            coordinate=cand.coordinate,
            content_hash=cand.content_hash,
            score=score,
            fraction_receipt=make_fraction_receipt(score),
            fusion_rank=idx,
        )
        for idx, (score, cand) in enumerate(scored, start=1)
    ]


@dataclass(frozen=True, slots=True)
class LexicalCandidateInput:
    coordinate: StableCoordinate
    content_hash: str
    is_exact: bool
    trigram_score: float | None = None
    fts_score: float | None = None


@dataclass(frozen=True, slots=True)
class LexicalFusionCandidate:
    coordinate: StableCoordinate
    content_hash: str
    is_exact: bool
    score: Fraction
    fraction_receipt: FractionReceipt
    lexical_rank: int
    raw_trigram_score: float | None
    raw_fts_score: float | None
    bucket_trigram_rank: int | None
    bucket_fts_rank: int | None


def _dedupe_lexical_inputs(
    candidates: Sequence[LexicalCandidateInput],
) -> dict[StableCoordinate, LexicalCandidateInput]:
    coord_map: dict[StableCoordinate, LexicalCandidateInput] = {}
    for cand in candidates:
        if cand.coordinate in coord_map:
            prev = coord_map[cand.coordinate]
            if prev.content_hash != cand.content_hash:
                raise EvidenceFusionError(
                    EvidenceFusionErrorCode.CONTENT_HASH_MISMATCH,
                    f"Content hash mismatch for coordinate {cand.coordinate}",
                )
            coord_map[cand.coordinate] = LexicalCandidateInput(
                coordinate=cand.coordinate,
                content_hash=cand.content_hash,
                is_exact=prev.is_exact or cand.is_exact,
                trigram_score=max(filter(None, [prev.trigram_score, cand.trigram_score]), default=None),
                fts_score=max(filter(None, [prev.fts_score, cand.fts_score]), default=None),
            )
        else:
            coord_map[cand.coordinate] = cand
    return coord_map


def _fuse_lexical_bucket(
    bucket: list[LexicalCandidateInput],
    rrf_k: int,
) -> list[LexicalFusionCandidate]:
    if not bucket:
        return []

    trigram_cands = [c for c in bucket if c.trigram_score is not None and c.trigram_score > 0]
    trigram_cands.sort(
        key=lambda c: (-c.trigram_score, stable_coordinate_sort_key(c.coordinate)),  # type: ignore[operator]
    )
    trigram_ranks = {c.coordinate: r for r, c in enumerate(trigram_cands, start=1)}

    fts_cands = [c for c in bucket if c.fts_score is not None and c.fts_score > 0]
    fts_cands.sort(
        key=lambda c: (-c.fts_score, stable_coordinate_sort_key(c.coordinate)),  # type: ignore[operator]
    )
    fts_ranks = {c.coordinate: r for r, c in enumerate(fts_cands, start=1)}

    bucket_results: list[tuple[Fraction, LexicalCandidateInput, int | None, int | None]] = []
    for cand in bucket:
        t_rank = trigram_ranks.get(cand.coordinate)
        f_rank = fts_ranks.get(cand.coordinate)
        total = Fraction(0, 1)
        if t_rank is not None:
            total += Fraction(1, rrf_k + t_rank)
        if f_rank is not None:
            total += Fraction(1, rrf_k + f_rank)
        bucket_results.append((total, cand, t_rank, f_rank))

    bucket_results.sort(
        key=lambda item: (-item[0], stable_coordinate_sort_key(item[1].coordinate)),
    )

    return [
        LexicalFusionCandidate(
            coordinate=cand.coordinate,
            content_hash=cand.content_hash,
            is_exact=cand.is_exact,
            score=score,
            fraction_receipt=make_fraction_receipt(score),
            lexical_rank=0,
            raw_trigram_score=cand.trigram_score,
            raw_fts_score=cand.fts_score,
            bucket_trigram_rank=t_rank,
            bucket_fts_rank=f_rank,
        )
        for score, cand, t_rank, f_rank in bucket_results
    ]


def fuse_lexical_subsearches(
    candidates: Sequence[LexicalCandidateInput],
    *,
    limit: int = 20,
    rrf_k: int = 60,
) -> list[LexicalFusionCandidate]:
    if limit <= 0:
        return []

    coord_map = _dedupe_lexical_inputs(candidates)
    exact_bucket = [c for c in coord_map.values() if c.is_exact]
    non_exact_bucket = [c for c in coord_map.values() if not c.is_exact]

    fused_exact = _fuse_lexical_bucket(exact_bucket, rrf_k)
    fused_non_exact = _fuse_lexical_bucket(non_exact_bucket, rrf_k)

    combined = fused_exact + fused_non_exact
    selected = combined[:limit]

    return [
        LexicalFusionCandidate(
            coordinate=cand.coordinate,
            content_hash=cand.content_hash,
            is_exact=cand.is_exact,
            score=cand.score,
            fraction_receipt=cand.fraction_receipt,
            lexical_rank=idx,
            raw_trigram_score=cand.raw_trigram_score,
            raw_fts_score=cand.raw_fts_score,
            bucket_trigram_rank=cand.bucket_trigram_rank,
            bucket_fts_rank=cand.bucket_fts_rank,
        )
        for idx, cand in enumerate(selected, start=1)
    ]


@dataclass(frozen=True, slots=True)
class CandidateStageSignal:
    rank: int
    score_decimal: str


@dataclass(frozen=True, slots=True)
class HybridFusionCandidate:
    coordinate: StableCoordinate
    content_hash: str
    lexical_signal: CandidateStageSignal | None = None
    dense_signal: CandidateStageSignal | None = None


@dataclass(frozen=True, slots=True)
class HybridFusionResult:
    coordinate: StableCoordinate
    content_hash: str
    score: Fraction
    fraction_receipt: FractionReceipt
    fusion_rank: int
    is_eligible_for_future_reranker: bool
    lexical_signal: CandidateStageSignal | None
    dense_signal: CandidateStageSignal | None


def _dedupe_hybrid_candidates(
    candidates: Sequence[HybridFusionCandidate],
) -> dict[StableCoordinate, HybridFusionCandidate]:
    coord_map: dict[StableCoordinate, HybridFusionCandidate] = {}
    for cand in candidates:
        if cand.coordinate in coord_map:
            prev = coord_map[cand.coordinate]
            if prev.content_hash != cand.content_hash:
                raise EvidenceFusionError(
                    EvidenceFusionErrorCode.CONTENT_HASH_MISMATCH,
                    f"Content hash mismatch for coordinate {cand.coordinate}",
                )
            lex = prev.lexical_signal or cand.lexical_signal
            dense = prev.dense_signal or cand.dense_signal
            coord_map[cand.coordinate] = HybridFusionCandidate(
                coordinate=cand.coordinate,
                content_hash=cand.content_hash,
                lexical_signal=lex,
                dense_signal=dense,
            )
        else:
            coord_map[cand.coordinate] = cand
    return coord_map


def fuse_hybrid_rrf(
    candidates: Sequence[HybridFusionCandidate],
    *,
    rrf_k: int = 60,
    hybrid_limit: int = 30,
    reranker_input_limit: int = 20,
) -> list[HybridFusionResult]:
    coord_map = _dedupe_hybrid_candidates(candidates)

    scored: list[tuple[Fraction, HybridFusionCandidate]] = []
    for cand in coord_map.values():
        total = Fraction(0, 1)
        if cand.lexical_signal is not None:
            if cand.lexical_signal.rank <= 0:
                raise EvidenceFusionError(EvidenceFusionErrorCode.INVALID_RANK, "Lexical rank must be > 0")
            total += Fraction(1, rrf_k + cand.lexical_signal.rank)
        if cand.dense_signal is not None:
            if cand.dense_signal.rank <= 0:
                raise EvidenceFusionError(EvidenceFusionErrorCode.INVALID_RANK, "Dense rank must be > 0")
            total += Fraction(1, rrf_k + cand.dense_signal.rank)
        scored.append((total, cand))

    scored.sort(
        key=lambda item: (-item[0], stable_coordinate_sort_key(item[1].coordinate)),
    )

    selected = scored[:hybrid_limit]
    return [
        HybridFusionResult(
            coordinate=cand.coordinate,
            content_hash=cand.content_hash,
            score=score,
            fraction_receipt=make_fraction_receipt(score),
            fusion_rank=idx,
            is_eligible_for_future_reranker=(idx <= reranker_input_limit),
            lexical_signal=cand.lexical_signal,
            dense_signal=cand.dense_signal,
        )
        for idx, (score, cand) in enumerate(selected, start=1)
    ]
