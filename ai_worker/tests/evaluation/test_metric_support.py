from decimal import Decimal

import pytest

from ai_worker.tasks.evaluation.metric_support import (
    BootstrapRatioCiDiagnostics,
    RatioContribution,
    canonical_ratio,
    percentile_cluster_bootstrap_ratio_ci,
    percentile_cluster_bootstrap_ratio_ci_with_diagnostics,
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


def test_cluster_bootstrap_with_diagnostics_handles_mixed_zero_denominator_clusters() -> None:
    groups = {
        "empty": (RatioContribution(0, 0),),
        "scored": (RatioContribution(1, 1),),
    }

    diag = percentile_cluster_bootstrap_ratio_ci_with_diagnostics(
        groups,
        seed=159,
        iterations=200,
        level=Decimal("0.95"),
    )

    assert isinstance(diag, BootstrapRatioCiDiagnostics)
    assert diag.total_replicates == 200
    assert diag.valid_replicates > 0
    assert diag.excluded_replicates > 0
    assert diag.valid_replicates + diag.excluded_replicates == diag.total_replicates
    assert diag.ci_lower is not None
    assert diag.ci_upper is not None
    assert Decimal(diag.valid_replicate_ratio) == Decimal(diag.valid_replicates) / Decimal(diag.total_replicates)


def test_cluster_bootstrap_with_diagnostics_handles_all_zero_denominator_clusters() -> None:
    groups = {
        "empty1": (RatioContribution(0, 0),),
        "empty2": (RatioContribution(0, 0),),
    }

    diag = percentile_cluster_bootstrap_ratio_ci_with_diagnostics(
        groups,
        seed=159,
        iterations=100,
        level=Decimal("0.95"),
    )

    assert diag == BootstrapRatioCiDiagnostics(
        ci_lower=None,
        ci_upper=None,
        total_replicates=100,
        valid_replicates=0,
        excluded_replicates=100,
        valid_replicate_ratio="0",
    )


def test_cluster_bootstrap_with_diagnostics_matches_legacy_helper_when_no_zero_denominator() -> None:
    groups = {
        "group-a": (RatioContribution(1, 2), RatioContribution(1, 1)),
        "group-b": (RatioContribution(0, 3),),
    }

    legacy = percentile_cluster_bootstrap_ratio_ci(
        groups,
        seed=159,
        iterations=200,
        level=Decimal("0.95"),
    )
    diag = percentile_cluster_bootstrap_ratio_ci_with_diagnostics(
        groups,
        seed=159,
        iterations=200,
        level=Decimal("0.95"),
    )

    assert (diag.ci_lower, diag.ci_upper) == legacy
    assert diag.total_replicates == 200
    assert diag.valid_replicates == 200
    assert diag.excluded_replicates == 0
    assert diag.valid_replicate_ratio == "1"
