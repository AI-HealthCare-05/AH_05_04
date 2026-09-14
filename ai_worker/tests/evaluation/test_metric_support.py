from decimal import Decimal

import pytest

from ai_worker.tasks.evaluation.metric_support import (
    RatioContribution,
    canonical_ratio,
    percentile_cluster_bootstrap_ratio_ci,
)


def test_canonical_ratio_uses_six_place_half_even_formatting() -> None:
    assert canonical_ratio(1, 3) == "0.333333"
    assert canonical_ratio(1, 8) == "0.125"
    assert canonical_ratio(0, 2) == "0"


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(-1, 1), (2, 1), (0, 0), (0, -1)],
)
def test_canonical_ratio_rejects_invalid_integer_counts(numerator: int, denominator: int) -> None:
    with pytest.raises(ValueError, match="ratio counts are invalid"):
        canonical_ratio(numerator, denominator)


def test_cluster_bootstrap_reaggregates_integer_ratio_contributions() -> None:
    groups = {
        "group-a": (RatioContribution(1, 2), RatioContribution(1, 1)),
        "group-b": (RatioContribution(0, 3),),
    }

    first = percentile_cluster_bootstrap_ratio_ci(
        groups,
        seed=159,
        iterations=200,
        level=Decimal("0.95"),
    )
    second = percentile_cluster_bootstrap_ratio_ci(
        groups,
        seed=159,
        iterations=200,
        level=Decimal("0.95"),
    )

    assert canonical_ratio(2, 6) == "0.333333"
    assert first == ("0", "0.666667")
    assert second == first


def test_cluster_bootstrap_rejects_zero_denominator_replicates() -> None:
    groups = {
        "empty": (RatioContribution(0, 0),),
        "scored": (RatioContribution(1, 1),),
    }

    with pytest.raises(ValueError, match="bootstrap replicate denominator is zero"):
        percentile_cluster_bootstrap_ratio_ci(
            groups,
            seed=1,
            iterations=100,
            level=Decimal("0.95"),
        )


def test_cluster_bootstrap_rejects_zero_denominator_group_before_sampling() -> None:
    groups = {
        "empty": (RatioContribution(0, 0),),
        "scored": (RatioContribution(1, 1),),
    }

    with pytest.raises(ValueError, match="bootstrap replicate denominator is zero"):
        percentile_cluster_bootstrap_ratio_ci(
            groups,
            seed=0,
            iterations=1,
            level=Decimal("0.95"),
        )
