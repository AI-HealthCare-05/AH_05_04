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
