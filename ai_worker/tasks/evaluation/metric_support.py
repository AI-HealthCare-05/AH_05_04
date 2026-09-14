from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

_SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class RatioContribution:
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if (
            type(self.numerator) is not int
            or type(self.denominator) is not int
            or self.numerator < 0
            or self.denominator < 0
            or self.numerator > self.denominator
        ):
            raise ValueError("ratio counts are invalid")


def _canonical_decimal(value: Decimal) -> str:
    quantized = value.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)
    if quantized == 0:
        return "0"
    return format(quantized, "f").rstrip("0").rstrip(".")


def canonical_ratio(numerator: int, denominator: int) -> str:
    contribution = RatioContribution(numerator, denominator)
    if contribution.denominator == 0:
        raise ValueError("ratio counts are invalid")
    return _canonical_decimal(Decimal(contribution.numerator) / Decimal(contribution.denominator))


def _percentile_bounds(estimates: list[Decimal], level: Decimal) -> tuple[str, str]:
    if not estimates:
        raise ValueError("percentile estimates must not be empty")
    if not Decimal(0) < level < Decimal(1):
        raise ValueError("confidence level must be between zero and one")
    estimates.sort()
    alpha = (Decimal(1) - level) / Decimal(2)
    last_index = Decimal(len(estimates) - 1)
    lower_index = int((last_index * alpha).to_integral_value(rounding=ROUND_FLOOR))
    upper_index = int((last_index * (Decimal(1) - alpha)).to_integral_value(rounding=ROUND_CEILING))
    return _canonical_decimal(estimates[lower_index]), _canonical_decimal(estimates[upper_index])


def percentile_cluster_bootstrap_ratio_ci(
    group_contributions: Mapping[str, tuple[RatioContribution, ...]],
    *,
    seed: int,
    iterations: int,
    level: Decimal,
) -> tuple[str, str]:
    if iterations <= 0:
        raise ValueError("bootstrap iterations must be positive")
    if not group_contributions or any(not values for values in group_contributions.values()):
        raise ValueError("bootstrap groups must contain contributions")
    if any(sum(item.denominator for item in values) == 0 for values in group_contributions.values()):
        raise ValueError("bootstrap replicate denominator is zero")

    group_ids = tuple(sorted(group_contributions, key=lambda value: value.encode("utf-16-be")))
    rng = random.Random(seed)
    estimates: list[Decimal] = []
    for _ in range(iterations):
        sampled_group_ids = tuple(group_ids[rng.randrange(len(group_ids))] for _ in group_ids)
        sampled = tuple(
            contribution for group_id in sampled_group_ids for contribution in group_contributions[group_id]
        )
        numerator = sum(item.numerator for item in sampled)
        denominator = sum(item.denominator for item in sampled)
        if denominator == 0:
            raise ValueError("bootstrap replicate denominator is zero")
        estimates.append(Decimal(numerator) / Decimal(denominator))
    return _percentile_bounds(estimates, level)
