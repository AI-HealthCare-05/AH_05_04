from decimal import Decimal

import pytest

from ai_worker.tasks.rag.source_ingestion.snapshot_policy import (
    SnapshotPolicyDecision,
    SnapshotPolicyFailureCode,
    SourceSnapshotPolicy,
    evaluate_snapshot_policy,
)


def test_allows_normal_result_as_pending_candidate() -> None:
    result = evaluate_snapshot_policy(
        record_count=10,
        rejected_record_count=0,
        policy=SourceSnapshotPolicy(),
    )

    assert result.decision is SnapshotPolicyDecision.ALLOW_PENDING
    assert result.rejection_rate == Decimal("0")
    assert result.manual_review_required is False
    assert result.failure_code is None
    assert result.snapshot_candidate_allowed is True


def test_default_policy_rejects_any_rejected_record() -> None:
    result = evaluate_snapshot_policy(
        record_count=10,
        rejected_record_count=1,
        policy=SourceSnapshotPolicy(),
    )

    assert result.decision is SnapshotPolicyDecision.REJECT
    assert result.failure_code is SnapshotPolicyFailureCode.REJECTION_LIMIT_EXCEEDED
    assert result.snapshot_candidate_allowed is False


def test_allows_rejections_at_both_hard_limits_but_requires_manual_review() -> None:
    result = evaluate_snapshot_policy(
        record_count=8,
        rejected_record_count=2,
        policy=SourceSnapshotPolicy(
            max_rejected_records=2,
            max_rejection_rate=Decimal("0.25"),
        ),
    )

    assert result.decision is SnapshotPolicyDecision.ALLOW_PENDING
    assert result.rejection_rate == Decimal("0.25")
    assert result.manual_review_required is True
    assert result.failure_code is None


@pytest.mark.parametrize(
    ("record_count", "rejected_record_count", "policy"),
    [
        (
            10,
            3,
            SourceSnapshotPolicy(
                max_rejected_records=2,
                max_rejection_rate=Decimal("0.5"),
            ),
        ),
        (
            10,
            2,
            SourceSnapshotPolicy(
                max_rejected_records=5,
                max_rejection_rate=Decimal("0.1"),
            ),
        ),
    ],
)
def test_rejects_when_either_hard_limit_is_exceeded(
    record_count: int,
    rejected_record_count: int,
    policy: SourceSnapshotPolicy,
) -> None:
    result = evaluate_snapshot_policy(
        record_count=record_count,
        rejected_record_count=rejected_record_count,
        policy=policy,
    )

    assert result.decision is SnapshotPolicyDecision.REJECT
    assert result.failure_code is SnapshotPolicyFailureCode.REJECTION_LIMIT_EXCEEDED
    assert result.snapshot_candidate_allowed is False


def test_rejects_empty_result_by_default() -> None:
    result = evaluate_snapshot_policy(
        record_count=0,
        rejected_record_count=0,
        policy=SourceSnapshotPolicy(),
    )

    assert result.decision is SnapshotPolicyDecision.REJECT
    assert result.rejection_rate == Decimal("0")
    assert result.failure_code is SnapshotPolicyFailureCode.EMPTY_RESULT


@pytest.mark.parametrize(
    ("record_count", "rejected_record_count"),
    [
        (-1, 0),
        (1, -1),
        (1, 2),
    ],
)
def test_rejects_invalid_record_counts(
    record_count: int,
    rejected_record_count: int,
) -> None:
    with pytest.raises(ValueError):
        evaluate_snapshot_policy(
            record_count=record_count,
            rejected_record_count=rejected_record_count,
            policy=SourceSnapshotPolicy(),
        )


@pytest.mark.parametrize(
    "max_rejection_rate",
    [
        Decimal("-0.01"),
        Decimal("1.01"),
    ],
)
def test_rejects_invalid_rejection_rate(max_rejection_rate: Decimal) -> None:
    with pytest.raises(ValueError, match="max_rejection_rate"):
        SourceSnapshotPolicy(max_rejection_rate=max_rejection_rate)
