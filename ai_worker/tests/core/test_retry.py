from collections.abc import Callable
from typing import cast

import pytest

from ai_worker.core.retry import (
    FailureCode,
    RetryDecisionReason,
    calculate_retry_decision,
)


def fixed_random(value: float) -> Callable[[], float]:
    """항상 같은 jitter 입력값을 반환하는 테스트용 함수입니다."""

    return lambda: value


@pytest.mark.parametrize(
    "failure_code",
    [
        "TIMEOUT",
        "DEPENDENCY_UNAVAILABLE",
    ],
)
def test_transient_failure_is_retryable(
    failure_code: FailureCode,
) -> None:
    decision = calculate_retry_decision(
        attempt_count=1,
        max_attempts=3,
        failure_code=failure_code,
        random_value=fixed_random(0),
    )

    assert decision.should_retry is True
    assert decision.final_failure is False
    assert decision.reason is RetryDecisionReason.RETRY_SCHEDULED
    assert decision.terminal_failure_code is None


def test_first_failure_uses_five_second_backoff() -> None:
    decision = calculate_retry_decision(
        attempt_count=1,
        max_attempts=3,
        failure_code="TIMEOUT",
        random_value=fixed_random(0),
    )

    assert decision.delay_seconds == pytest.approx(5.0)


def test_second_failure_uses_ten_second_backoff() -> None:
    decision = calculate_retry_decision(
        attempt_count=2,
        max_attempts=3,
        failure_code="DEPENDENCY_UNAVAILABLE",
        random_value=fixed_random(0),
    )

    assert decision.delay_seconds == pytest.approx(10.0)


def test_backoff_is_capped_at_sixty_seconds() -> None:
    decision = calculate_retry_decision(
        attempt_count=6,
        max_attempts=10,
        failure_code="TIMEOUT",
        random_value=fixed_random(0),
    )

    assert decision.delay_seconds == pytest.approx(60.0)


def test_positive_jitter_is_calculated_from_injected_random_value() -> None:
    decision = calculate_retry_decision(
        attempt_count=1,
        max_attempts=3,
        failure_code="TIMEOUT",
        # 최대 20% jitter의 절반이므로 5초에 10%를 더합니다.
        random_value=fixed_random(0.5),
    )

    assert decision.delay_seconds == pytest.approx(5.5)


def test_maximum_positive_jitter_adds_twenty_percent() -> None:
    decision = calculate_retry_decision(
        attempt_count=1,
        max_attempts=3,
        failure_code="TIMEOUT",
        # 난수 1은 승인된 최대 jitter 20%를 적용합니다.
        random_value=fixed_random(1),
    )

    assert decision.delay_seconds == pytest.approx(6.0)


@pytest.mark.parametrize(
    "failure_code",
    [
        "INVALID_INPUT",
        "UNSUPPORTED_SCHEMA",
        "SAFETY_VALIDATION_FAILED",
        "RETRY_EXHAUSTED",
        "INTERNAL_ERROR",
    ],
)
def test_permanent_failure_is_not_retried(
    failure_code: FailureCode,
) -> None:
    decision = calculate_retry_decision(
        attempt_count=1,
        max_attempts=3,
        failure_code=failure_code,
        random_value=fixed_random(0),
    )

    assert decision.should_retry is False
    assert decision.delay_seconds is None
    assert decision.final_failure is True
    assert decision.reason is RetryDecisionReason.NON_RETRYABLE
    assert decision.terminal_failure_code == failure_code


def test_max_attempts_returns_retry_exhausted() -> None:
    decision = calculate_retry_decision(
        attempt_count=3,
        max_attempts=3,
        failure_code="TIMEOUT",
        random_value=fixed_random(0),
    )

    assert decision.should_retry is False
    assert decision.delay_seconds is None
    assert decision.final_failure is True
    assert decision.reason is RetryDecisionReason.ATTEMPTS_EXHAUSTED
    assert decision.terminal_failure_code == "RETRY_EXHAUSTED"


@pytest.mark.parametrize(
    ("attempt_count", "max_attempts"),
    [
        (0, 3),
        (1, 0),
        (4, 3),
    ],
)
def test_invalid_attempt_boundaries_are_rejected(
    attempt_count: int,
    max_attempts: int,
) -> None:
    with pytest.raises(ValueError):
        calculate_retry_decision(
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            failure_code="TIMEOUT",
            random_value=fixed_random(0),
        )


@pytest.mark.parametrize("attempt_count", [True, 1.5])
def test_non_integer_attempt_count_is_rejected(attempt_count: object) -> None:
    with pytest.raises(TypeError, match="attempt_count"):
        calculate_retry_decision(
            # 런타임 입력 방어를 검증하기 위해 의도적으로 잘못된 타입을 전달합니다.
            attempt_count=cast(int, attempt_count),
            max_attempts=3,
            failure_code="TIMEOUT",
            random_value=fixed_random(0),
        )


@pytest.mark.parametrize("max_attempts", [False, 3.5])
def test_non_integer_max_attempts_is_rejected(max_attempts: object) -> None:
    with pytest.raises(TypeError, match="max_attempts"):
        calculate_retry_decision(
            attempt_count=1,
            # 런타임 입력 방어를 검증하기 위해 의도적으로 잘못된 타입을 전달합니다.
            max_attempts=cast(int, max_attempts),
            failure_code="TIMEOUT",
            random_value=fixed_random(0),
        )


@pytest.mark.parametrize(
    "sampled_value",
    [
        -0.1,
        1.1,
        float("inf"),
    ],
)
def test_invalid_jitter_value_is_rejected(sampled_value: float) -> None:
    with pytest.raises(ValueError, match="random_value"):
        calculate_retry_decision(
            attempt_count=1,
            max_attempts=3,
            failure_code="TIMEOUT",
            random_value=fixed_random(sampled_value),
        )


def test_unknown_failure_code_is_rejected() -> None:
    unknown_code = cast(FailureCode, "UNKNOWN")

    with pytest.raises(ValueError, match="failure_code"):
        calculate_retry_decision(
            attempt_count=1,
            max_attempts=3,
            failure_code=unknown_code,
            random_value=fixed_random(0),
        )
