"""DB와 메시지 브로커에 의존하지 않는 Worker 재시도 계산 로직입니다."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

# random.random과 같은 난수 공급자를 외부에서 주입합니다.
# 테스트에서는 고정값을 반환하는 함수를 전달할 수 있습니다.
type RandomValueProvider = Callable[[], float]

# 승인된 공통 failure_code만 사용합니다.
type FailureCode = Literal[
    "TIMEOUT",
    "DEPENDENCY_UNAVAILABLE",
    "INVALID_INPUT",
    "UNSUPPORTED_SCHEMA",
    "SAFETY_VALIDATION_FAILED",
    "RETRY_EXHAUSTED",
    "INTERNAL_ERROR",
]

INITIAL_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 60.0
MAX_JITTER_RATIO = 0.2

ALL_FAILURE_CODES: frozenset[FailureCode] = frozenset(
    {
        "TIMEOUT",
        "DEPENDENCY_UNAVAILABLE",
        "INVALID_INPUT",
        "UNSUPPORTED_SCHEMA",
        "SAFETY_VALIDATION_FAILED",
        "RETRY_EXHAUSTED",
        "INTERNAL_ERROR",
    }
)

# rate limit과 일시적 Provider 오류는 adapter에서
# DEPENDENCY_UNAVAILABLE로 정규화한 뒤 이 함수에 전달합니다.
RETRYABLE_FAILURE_CODES: frozenset[FailureCode] = frozenset(
    {
        "TIMEOUT",
        "DEPENDENCY_UNAVAILABLE",
    }
)


class RetryDecisionReason(StrEnum):
    """재시도 계산 결과의 내부 판정 사유입니다."""

    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    NON_RETRYABLE = "NON_RETRYABLE"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """외부 I/O 없이 계산한 재시도 결과입니다."""

    should_retry: bool
    delay_seconds: float | None
    final_failure: bool
    reason: RetryDecisionReason
    terminal_failure_code: FailureCode | None


def calculate_retry_decision(
    *,
    attempt_count: int,
    max_attempts: int,
    failure_code: FailureCode,
    random_value: RandomValueProvider,
) -> RetryDecision:
    """재시도 여부와 다음 실행까지의 지연시간을 계산합니다.

    `attempt_count`는 방금 실패한 실행 번호이며 최초 실행이 1입니다.
    `max_attempts`는 최초 실행을 포함한 전체 허용 횟수입니다.

    이 함수는 DB 저장, Redis 발행, 실제 대기, 시스템 시간 조회를
    수행하지 않습니다.
    """

    _validate_attempts(
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )

    if failure_code not in ALL_FAILURE_CODES:
        raise ValueError("승인되지 않은 failure_code입니다.")

    # 입력·schema·Safety 오류와 영구 오류는 횟수가 남아 있어도 종료합니다.
    if failure_code not in RETRYABLE_FAILURE_CODES:
        return RetryDecision(
            should_retry=False,
            delay_seconds=None,
            final_failure=True,
            reason=RetryDecisionReason.NON_RETRYABLE,
            terminal_failure_code=failure_code,
        )

    # 최초 실행을 포함한 최대 횟수에 도달하면 재시도를 예약하지 않습니다.
    if attempt_count >= max_attempts:
        return RetryDecision(
            should_retry=False,
            delay_seconds=None,
            final_failure=True,
            reason=RetryDecisionReason.ATTEMPTS_EXHAUSTED,
            terminal_failure_code="RETRY_EXHAUSTED",
        )

    base_delay = _calculate_base_delay(attempt_count=attempt_count)
    delay_seconds = _add_positive_jitter(
        base_delay=base_delay,
        random_value=random_value,
    )

    return RetryDecision(
        should_retry=True,
        delay_seconds=delay_seconds,
        final_failure=False,
        reason=RetryDecisionReason.RETRY_SCHEDULED,
        terminal_failure_code=None,
    )


def _calculate_base_delay(*, attempt_count: int) -> float:
    """승인된 지수 backoff를 최대 60초까지 계산합니다."""

    # 5, 10, 20, 40, 60 순서이며 이후에도 60초를 유지합니다.
    # 매우 큰 지수 계산을 피하기 위해 4단계 이후의 지수는 고정합니다.
    exponent = min(attempt_count - 1, 4)
    calculated_delay = INITIAL_BACKOFF_SECONDS * (2**exponent)

    return min(calculated_delay, MAX_BACKOFF_SECONDS)


def _add_positive_jitter(
    *,
    base_delay: float,
    random_value: RandomValueProvider,
) -> float:
    """기본 지연에 0~20% 범위의 양의 jitter를 추가합니다."""

    sampled_value = random_value()

    # random.random과 동일하게 0 이상 1 이하의 유한한 값만 허용합니다.
    if (
        isinstance(sampled_value, bool)
        or not isinstance(sampled_value, int | float)
        or not math.isfinite(sampled_value)
        or not 0 <= sampled_value <= 1
    ):
        raise ValueError("random_value는 0 이상 1 이하의 유한한 숫자를 반환해야 합니다.")

    jitter_ratio = MAX_JITTER_RATIO * float(sampled_value)
    return base_delay * (1 + jitter_ratio)


def _validate_attempts(
    *,
    attempt_count: int,
    max_attempts: int,
) -> None:
    """시도 횟수의 타입과 범위를 검증합니다."""

    if isinstance(attempt_count, bool) or not isinstance(attempt_count, int):
        raise TypeError("attempt_count는 정수여야 합니다.")

    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise TypeError("max_attempts는 정수여야 합니다.")

    if attempt_count < 1:
        raise ValueError("attempt_count는 1 이상이어야 합니다.")

    if max_attempts < 1:
        raise ValueError("max_attempts는 1 이상이어야 합니다.")

    if attempt_count > max_attempts:
        raise ValueError("attempt_count는 max_attempts보다 클 수 없습니다.")
