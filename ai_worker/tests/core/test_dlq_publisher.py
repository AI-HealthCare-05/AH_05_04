"""DLQ Outbox Publisher 상태 전이 테스트입니다."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ai_worker.core.dlq import (
    ClaimedDlqEvent,
    DlqOutboxPublisher,
    DlqPublishReport,
)
from ai_worker.core.quarantine import (
    DeadLetterEnvelope,
    QuarantineFailureCode,
)


def build_claimed_event(
    *,
    attempt_count: int = 1,
) -> ClaimedDlqEvent:
    envelope = DeadLetterEnvelope(
        event_id=uuid4(),
        quarantine_id=uuid4(),
        stream_entry_id="1000-0",
        message_digest="a" * 64,
        failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
        original_schema_version=None,
        trace_id=None,
    )

    return ClaimedDlqEvent(
        envelope=envelope,
        claim_token=uuid4().hex,
        attempt_count=attempt_count,
    )


@pytest.mark.asyncio
async def test_dlq_publisher_commits_claim_before_stream_publish() -> None:
    now = datetime.now(UTC)
    claimed = build_claimed_event()
    events: list[str] = []

    async def claim_next(
        **_: object,
    ) -> ClaimedDlqEvent:
        events.append("claim")
        return claimed

    async def publish(
        _: DeadLetterEnvelope,
    ) -> str:
        events.append("publish")
        return "9000-0"

    repository = SimpleNamespace(
        claim_next=AsyncMock(side_effect=claim_next),
        mark_published=AsyncMock(side_effect=lambda **_: events.append("mark_published")),
        reschedule=AsyncMock(),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: events.append("commit")),
        rollback=AsyncMock(),
    )
    stream = SimpleNamespace(publish=AsyncMock(side_effect=publish))
    alerter = SimpleNamespace(notify_publish_failure=AsyncMock())

    publisher = DlqOutboxPublisher(
        repository=repository,
        transaction=transaction,
        stream=stream,
        alerter=alerter,
        claim_ttl=timedelta(seconds=30),
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    result = await publisher.run_once()

    assert result == DlqPublishReport(
        event_id=claimed.envelope.event_id,
        stream_message_id="9000-0",
        published=True,
        retry_scheduled=False,
        alert_required=False,
    )
    assert events == [
        "claim",
        "commit",
        "publish",
        "mark_published",
        "commit",
    ]

    repository.claim_next.assert_awaited_once_with(
        now=now,
        claim_expires_at=now + timedelta(seconds=30),
    )
    repository.mark_published.assert_awaited_once_with(
        event_id=claimed.envelope.event_id,
        claim_token=claimed.claim_token,
        published_at=now,
    )
    repository.reschedule.assert_not_awaited()
    transaction.rollback.assert_not_awaited()
    alerter.notify_publish_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_dlq_publisher_returns_idle_when_no_event_is_due() -> None:
    now = datetime.now(UTC)

    repository = SimpleNamespace(
        claim_next=AsyncMock(return_value=None),
        mark_published=AsyncMock(),
        reschedule=AsyncMock(),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    stream = SimpleNamespace(publish=AsyncMock())
    alerter = SimpleNamespace(notify_publish_failure=AsyncMock())

    publisher = DlqOutboxPublisher(
        repository=repository,
        transaction=transaction,
        stream=stream,
        alerter=alerter,
        claim_ttl=timedelta(seconds=30),
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    result = await publisher.run_once()

    assert result == DlqPublishReport(
        event_id=None,
        stream_message_id=None,
        published=False,
        retry_scheduled=False,
        alert_required=False,
    )

    transaction.commit.assert_awaited_once()
    transaction.rollback.assert_not_awaited()
    stream.publish.assert_not_awaited()
    repository.mark_published.assert_not_awaited()
    repository.reschedule.assert_not_awaited()
    alerter.notify_publish_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_dlq_publish_failure_reschedules_same_event_and_alerts() -> None:
    now = datetime.now(UTC)
    claimed = build_claimed_event(attempt_count=10)

    repository = SimpleNamespace(
        claim_next=AsyncMock(return_value=claimed),
        mark_published=AsyncMock(),
        reschedule=AsyncMock(),
    )
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    stream = SimpleNamespace(
        publish=AsyncMock(side_effect=RuntimeError("synthetic provider detail must not be stored"))
    )
    alerter = SimpleNamespace(notify_publish_failure=AsyncMock())

    publisher = DlqOutboxPublisher(
        repository=repository,
        transaction=transaction,
        stream=stream,
        alerter=alerter,
        claim_ttl=timedelta(seconds=30),
        clock=lambda: now,
        random_value=lambda: 0.0,
    )

    result = await publisher.run_once()

    assert result == DlqPublishReport(
        event_id=claimed.envelope.event_id,
        stream_message_id=None,
        published=False,
        retry_scheduled=True,
        alert_required=True,
    )

    repository.reschedule.assert_awaited_once_with(
        event_id=claimed.envelope.event_id,
        claim_token=claimed.claim_token,
        available_at=now + timedelta(seconds=300),
        error_code="DLQ_PUBLISH_FAILED",
    )
    repository.mark_published.assert_not_awaited()

    # 선점 commit과 재예약 commit이 각각 수행됩니다.
    assert transaction.commit.await_count == 2
    transaction.rollback.assert_not_awaited()

    alerter.notify_publish_failure.assert_awaited_once_with(
        event_id=claimed.envelope.event_id,
        attempt_count=10,
    )
