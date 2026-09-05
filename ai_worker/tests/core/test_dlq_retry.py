"""DLQ Outbox 발행 재시도 정책 테스트입니다."""

from typing import cast

import pytest

from ai_worker.core.dlq import (
    DlqRetryDecision,
    calculate_dlq_retry_decision,
)


@pytest.mark.parametrize(
    ("attempt_count", "expected_delay"),
    [
        (1, 5.0),
        (2, 10.0),
        (3, 20.0),
        (4, 40.0),
        (5, 80.0),
        (6, 160.0),
        (7, 300.0),
        (100, 300.0),
    ],
)
def test_dlq_retry_uses_exponential_backoff_with_300_second_cap(
    attempt_count: int,
    expected_delay: float,
) -> None:
    decision = calculate_dlq_retry_decision(
        attempt_count=attempt_count,
        random_value=lambda: 0.0,
    )

    assert decision.delay_seconds == expected_delay


def test_dlq_retry_adds_up_to_twenty_percent_positive_jitter() -> None:
    decision = calculate_dlq_retry_decision(
        attempt_count=1,
        random_value=lambda: 1.0,
    )

    assert decision.delay_seconds == 6.0


@pytest.mark.parametrize(
    ("attempt_count", "alert_required"),
    [
        (1, False),
        (9, False),
        (10, True),
        (100, True),
    ],
)
def test_dlq_retry_alerts_from_tenth_consecutive_failure(
    attempt_count: int,
    alert_required: bool,
) -> None:
    decision = calculate_dlq_retry_decision(
        attempt_count=attempt_count,
        random_value=lambda: 0.0,
    )

    assert decision.alert_required is alert_required


def test_dlq_retry_decision_has_no_terminal_disposition() -> None:
    decision = calculate_dlq_retry_decision(
        attempt_count=1_000,
        random_value=lambda: 0.0,
    )

    assert decision == DlqRetryDecision(
        delay_seconds=300.0,
        alert_required=True,
    )
    assert not hasattr(decision, "terminal")


@pytest.mark.parametrize(
    "attempt_count",
    [
        0,
        -1,
    ],
)
def test_dlq_retry_rejects_invalid_attempt_count(
    attempt_count: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="attempt_count",
    ):
        calculate_dlq_retry_decision(
            attempt_count=attempt_count,
            random_value=lambda: 0.0,
        )


@pytest.mark.parametrize(
    "attempt_count",
    [
        True,
        1.0,
        "1",
    ],
)
def test_dlq_retry_rejects_non_integer_attempt_count(
    attempt_count: object,
) -> None:
    with pytest.raises(
        TypeError,
        match="attempt_count",
    ):
        calculate_dlq_retry_decision(
            attempt_count=attempt_count,  # type: ignore[arg-type]
            random_value=lambda: 0.0,
        )


@pytest.mark.parametrize(
    "random_value",
    [
        -0.1,
        1.1,
        float("nan"),
        float("inf"),
        True,
        "0.5",
    ],
)
def test_dlq_retry_rejects_invalid_random_value(
    random_value: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="random_value",
    ):
        calculate_dlq_retry_decision(
            attempt_count=1,
            random_value=lambda: cast(float, random_value),
        )
