"""DLQ Outbox 발행과 재시도에 사용하는 핵심 계약입니다."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from ai_worker.core.consumer_execution import Transaction
from ai_worker.core.quarantine import DeadLetterEnvelope

DLQ_INITIAL_BACKOFF_SECONDS = 5.0
DLQ_MAX_BACKOFF_SECONDS = 300.0
DLQ_MAX_JITTER_RATIO = 0.2
DLQ_ALERT_ATTEMPT_THRESHOLD = 10

type DlqRandomValueProvider = Callable[[], float]


@dataclass(frozen=True, slots=True)
class DlqRetryDecision:
    """DLQ 발행 실패 후 다음 시도와 경보 여부입니다."""

    delay_seconds: float
    alert_required: bool


@dataclass(frozen=True, slots=True)
class ClaimedDlqEvent:
    """발행을 위해 fencing token으로 선점된 DLQ Outbox입니다."""

    envelope: DeadLetterEnvelope
    claim_token: str
    attempt_count: int

    def __post_init__(self) -> None:
        if not self.claim_token.strip():
            raise ValueError("claim_token은 비어 있을 수 없습니다.")

        _validate_attempt_count(self.attempt_count)


@dataclass(frozen=True, slots=True)
class DlqPublishReport:
    """DLQ Publisher 한 번의 실행 결과입니다."""

    event_id: UUID | None
    stream_message_id: str | None
    published: bool
    retry_scheduled: bool
    alert_required: bool


class DlqOutboxRepository(Protocol):
    """DLQ Outbox의 claim·발행 완료·재예약 저장 계약입니다."""

    async def claim_next(
        self,
        *,
        now: datetime,
        claim_expires_at: datetime,
    ) -> ClaimedDlqEvent | None:
        """발행 가능한 row 하나를 fencing token으로 선점합니다."""
        ...

    async def mark_published(
        self,
        *,
        event_id: UUID,
        claim_token: str,
        published_at: datetime,
    ) -> None:
        """현재 claim 소유자만 PUBLISHED로 전환합니다."""
        ...

    async def reschedule(
        self,
        *,
        event_id: UUID,
        claim_token: str,
        available_at: datetime,
        error_code: str,
    ) -> None:
        """현재 claim 소유자만 동일 event를 다음 시각으로 재예약합니다."""
        ...


class DeadLetterStreamPublisher(Protocol):
    """비민감 Dead-letter envelope 발행 계약입니다."""

    async def publish(
        self,
        envelope: DeadLetterEnvelope,
    ) -> str:
        """DLQ Stream entry ID를 반환합니다."""
        ...


class DlqFailureAlerter(Protocol):
    """반복 DLQ 발행 실패 경보 계약입니다."""

    async def notify_publish_failure(
        self,
        *,
        event_id: UUID,
        attempt_count: int,
    ) -> None:
        """운영 경보를 발생시킵니다."""
        ...


class DlqOutboxPublisher:
    """DLQ Outbox를 선점하고 Stream 발행 결과를 저장합니다."""

    def __init__(
        self,
        *,
        repository: DlqOutboxRepository,
        transaction: Transaction,
        stream: DeadLetterStreamPublisher,
        alerter: DlqFailureAlerter,
        claim_ttl: timedelta,
        clock: Callable[[], datetime],
        random_value: DlqRandomValueProvider,
    ) -> None:
        if claim_ttl <= timedelta(0):
            raise ValueError("claim_ttl은 0초보다 커야 합니다.")

        self._repository = repository
        self._transaction = transaction
        self._stream = stream
        self._alerter = alerter
        self._claim_ttl = claim_ttl
        self._clock = clock
        self._random_value = random_value

    async def run_once(self) -> DlqPublishReport:
        """발행 가능한 DLQ Outbox 하나를 처리합니다."""

        now = self._clock()

        try:
            claimed = await self._repository.claim_next(
                now=now,
                claim_expires_at=now + self._claim_ttl,
            )
            # 외부 Stream 호출 중 DB row lock을 유지하지 않도록
            # claim 상태를 먼저 commit합니다.
            await self._transaction.commit()
        except BaseException:
            await self._rollback_safely()
            raise

        if claimed is None:
            return DlqPublishReport(
                event_id=None,
                stream_message_id=None,
                published=False,
                retry_scheduled=False,
                alert_required=False,
            )

        try:
            stream_message_id = await self._stream.publish(claimed.envelope)
        except Exception:
            return await self._reschedule_failed_publish(
                claimed=claimed,
                now=now,
            )

        try:
            await self._repository.mark_published(
                event_id=claimed.envelope.event_id,
                claim_token=claimed.claim_token,
                published_at=now,
            )
            await self._transaction.commit()
        except BaseException:
            # XADD 성공 뒤 DB 갱신 실패 시 같은 event_id로 재발행될 수
            # 있습니다. Consumer가 event_id 기준으로 중복을 처리합니다.
            await self._rollback_safely()
            raise

        return DlqPublishReport(
            event_id=claimed.envelope.event_id,
            stream_message_id=stream_message_id,
            published=True,
            retry_scheduled=False,
            alert_required=False,
        )

    async def _reschedule_failed_publish(
        self,
        *,
        claimed: ClaimedDlqEvent,
        now: datetime,
    ) -> DlqPublishReport:
        decision = calculate_dlq_retry_decision(
            attempt_count=claimed.attempt_count,
            random_value=self._random_value,
        )

        try:
            await self._repository.reschedule(
                event_id=claimed.envelope.event_id,
                claim_token=claimed.claim_token,
                available_at=now + timedelta(seconds=decision.delay_seconds),
                error_code="DLQ_PUBLISH_FAILED",
            )
            await self._transaction.commit()
        except BaseException:
            await self._rollback_safely()
            raise

        if decision.alert_required:
            await self._alerter.notify_publish_failure(
                event_id=claimed.envelope.event_id,
                attempt_count=claimed.attempt_count,
            )

        return DlqPublishReport(
            event_id=claimed.envelope.event_id,
            stream_message_id=None,
            published=False,
            retry_scheduled=True,
            alert_required=decision.alert_required,
        )

    async def _rollback_safely(self) -> None:
        try:
            await self._transaction.rollback()
        except Exception:
            return


def calculate_dlq_retry_decision(
    *,
    attempt_count: int,
    random_value: DlqRandomValueProvider,
) -> DlqRetryDecision:
    """DLQ 발행 실패 횟수를 기준으로 다음 재시도를 계산합니다.

    DLQ는 자동 폐기하거나 terminal 상태로 전환하지 않습니다.
    5초부터 시작하는 지수 backoff를 300초로 제한하고,
    여기에 0~20% 범위의 양의 jitter를 추가합니다.
    """

    _validate_attempt_count(attempt_count)

    # 7번째 실패부터 기본 지연이 300초 상한에 도달합니다.
    # 매우 큰 attempt_count의 거대한 정수 연산도 방지합니다.
    exponent = min(attempt_count - 1, 6)
    base_delay = min(
        DLQ_INITIAL_BACKOFF_SECONDS * (2**exponent),
        DLQ_MAX_BACKOFF_SECONDS,
    )
    sampled_value = _validated_random_value(random_value)

    delay_seconds = base_delay * (1 + DLQ_MAX_JITTER_RATIO * sampled_value)

    return DlqRetryDecision(
        delay_seconds=delay_seconds,
        alert_required=(attempt_count >= DLQ_ALERT_ATTEMPT_THRESHOLD),
    )


def _validate_attempt_count(attempt_count: int) -> None:
    if isinstance(attempt_count, bool) or not isinstance(
        attempt_count,
        int,
    ):
        raise TypeError("attempt_count는 정수여야 합니다.")

    if attempt_count < 1:
        raise ValueError("attempt_count는 1 이상이어야 합니다.")


def _validated_random_value(
    random_value: DlqRandomValueProvider,
) -> float:
    sampled_value = random_value()
    error_message = "random_value는 0 이상 1 이하의 유한한 숫자를 반환해야 합니다."

    if isinstance(sampled_value, bool) or not isinstance(
        sampled_value,
        int | float,
    ):
        raise ValueError(error_message)

    try:
        normalized_value = float(sampled_value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(error_message) from exc

    if not math.isfinite(normalized_value) or not 0 <= normalized_value <= 1:
        raise ValueError(error_message)

    return normalized_value
