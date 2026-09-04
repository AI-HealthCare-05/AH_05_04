from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN


_SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    required_ids: tuple[str, ...]
    relevant_ids: tuple[str, ...]
    ranked_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AggregatedMetric:
    value: Decimal
    numerator: int
    denominator: int
    reason_code: str | None


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)


def metric_scores(observation: RetrievalObservation, *, k: int = 5) -> dict[str, Decimal] | None:
    """Return deterministic per-case Retrieval@K diagnostics, or None for no Recall denominator."""

    required = set(observation.required_ids)
    if not required:
        return None
    relevant = set(observation.relevant_ids)
    ranked = observation.ranked_ids[:k]
    if len(ranked) != len(set(ranked)):
        raise ValueError("ranked evidence ids must be unique")

    required_hits = required.intersection(ranked)
    relevant_hits = relevant.intersection(ranked)
    first_rank = next((index for index, item in enumerate(ranked, 1) if item in relevant), None)
    dcg = sum(
        (Decimal(1) / Decimal(str(math.log2(index + 1))) for index, item in enumerate(ranked, 1) if item in relevant),
        Decimal(0),
    )
    ideal_count = min(len(relevant), k)
    idcg = sum(
        (Decimal(1) / Decimal(str(math.log2(index + 1))) for index in range(1, ideal_count + 1)),
        Decimal(0),
    )
    return {
        "RECALL_AT_5": _quantize(Decimal(len(required_hits)) / Decimal(len(required))),
        "PRECISION_AT_5": _quantize(Decimal(len(relevant_hits)) / Decimal(k)),
        "MRR": Decimal(0) if first_rank is None else _quantize(Decimal(1) / Decimal(first_rank)),
        "NDCG_AT_5": Decimal(0) if idcg == 0 else _quantize(dcg / idcg),
        "NO_HIT_RATE": Decimal(0) if relevant_hits else Decimal(1),
    }


def aggregate_metric_scores(
    score_sets: list[dict[str, Decimal] | None],
    *,
    minimum_case_count: int,
) -> dict[str, AggregatedMetric]:
    """Aggregate per-case Retrieval diagnostics without treating insufficient data as a pass."""

    completed = [scores for scores in score_sets if scores is not None]
    if not completed:
        return {}
    case_count = len(completed)
    reason = "MINIMUM_CASE_COUNT_NOT_MET" if case_count < minimum_case_count else None
    aggregates: dict[str, AggregatedMetric] = {}
    for metric_id in completed[0]:
        values = [scores[metric_id] for scores in completed]
        if metric_id == "PRECISION_AT_5":
            numerator = int(sum(values, Decimal(0)) * Decimal(5))
            denominator = case_count * 5
        elif metric_id in {"RECALL_AT_5", "NO_HIT_RATE"}:
            numerator = int(sum(values, Decimal(0)))
            denominator = case_count
        else:
            numerator = sum(value > 0 for value in values)
            denominator = case_count
        aggregates[metric_id] = AggregatedMetric(
            value=_quantize(sum(values, Decimal(0)) / Decimal(case_count)),
            numerator=numerator,
            denominator=denominator,
            reason_code=reason,
        )
    return aggregates


def observations_from_case_results(
    gold_by_case: dict[str, tuple[tuple[str, ...], tuple[str, ...]]],
    ranked_by_case: dict[str, tuple[str, ...]],
) -> tuple[RetrievalObservation, ...]:
    """Bind immutable Gold IDs to ranked output; mismatched case sets are invalid."""

    if set(gold_by_case) != set(ranked_by_case):
        raise ValueError("gold and ranked case sets must match")
    return tuple(
        RetrievalObservation(
            required_ids=gold_by_case[case_id][0],
            relevant_ids=gold_by_case[case_id][1],
            ranked_ids=ranked_by_case[case_id],
        )
        for case_id in sorted(gold_by_case)
    )
