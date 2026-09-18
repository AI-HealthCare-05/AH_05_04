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


@dataclass(frozen=True, slots=True)
class BootstrapRatioCiDiagnostics:
    ci_lower: str | None
    ci_upper: str | None
    total_replicates: int
    valid_replicates: int
    excluded_replicates: int
    valid_replicate_ratio: str


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


def _cluster_bootstrap_ratio_replicates(
    group_contributions: Mapping[str, tuple[RatioContribution, ...]],
    *,
    seed: int,
    iterations: int,
    allow_zero_denominator_replicates: bool,
) -> tuple[list[Decimal], int, int]:
    if iterations <= 0:
        raise ValueError("bootstrap iterations must be positive")
    if not group_contributions or any(not values for values in group_contributions.values()):
        raise ValueError("bootstrap groups must contain contributions")
    if not allow_zero_denominator_replicates and any(
        sum(item.denominator for item in values) == 0 for values in group_contributions.values()
    ):
        raise ValueError("bootstrap replicate denominator is zero")

    group_ids = tuple(sorted(group_contributions, key=lambda value: value.encode("utf-16-be")))
    rng = random.Random(seed)
    estimates: list[Decimal] = []
    valid_replicates = 0
    excluded_replicates = 0
    for _ in range(iterations):
        sampled_group_ids = tuple(group_ids[rng.randrange(len(group_ids))] for _ in group_ids)
        sampled = tuple(
            contribution for group_id in sampled_group_ids for contribution in group_contributions[group_id]
        )
        numerator = sum(item.numerator for item in sampled)
        denominator = sum(item.denominator for item in sampled)
        if denominator == 0:
            if not allow_zero_denominator_replicates:
                raise ValueError("bootstrap replicate denominator is zero")
            excluded_replicates += 1
            continue
        valid_replicates += 1
        estimates.append(Decimal(numerator) / Decimal(denominator))
    return estimates, valid_replicates, excluded_replicates


def percentile_cluster_bootstrap_ratio_ci(
    group_contributions: Mapping[str, tuple[RatioContribution, ...]],
    *,
    seed: int,
    iterations: int,
    level: Decimal,
) -> tuple[str, str]:
    estimates, _, _ = _cluster_bootstrap_ratio_replicates(
        group_contributions,
        seed=seed,
        iterations=iterations,
        allow_zero_denominator_replicates=False,
    )
    return _percentile_bounds(estimates, level)


def percentile_cluster_bootstrap_ratio_ci_with_diagnostics(
    group_contributions: Mapping[str, tuple[RatioContribution, ...]],
    *,
    seed: int,
    iterations: int,
    level: Decimal,
) -> BootstrapRatioCiDiagnostics:
    estimates, valid_replicates, excluded_replicates = _cluster_bootstrap_ratio_replicates(
        group_contributions,
        seed=seed,
        iterations=iterations,
        allow_zero_denominator_replicates=True,
    )
    total_replicates = iterations
    valid_ratio = _canonical_decimal(Decimal(valid_replicates) / Decimal(total_replicates))
    if valid_replicates > 0:
        ci_lower, ci_upper = _percentile_bounds(estimates, level)
    else:
        ci_lower, ci_upper = None, None
    return BootstrapRatioCiDiagnostics(
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        total_replicates=total_replicates,
        valid_replicates=valid_replicates,
        excluded_replicates=excluded_replicates,
        valid_replicate_ratio=valid_ratio,
    )
