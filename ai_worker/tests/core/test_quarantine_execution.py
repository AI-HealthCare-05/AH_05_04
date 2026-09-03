"""Quarantine 저장·commit·ACK 실행 경계 테스트입니다."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ai_worker.core.quarantine import (
    QuarantineExecution,
    QuarantineFailureCode,
    QuarantineReceipt,
    QuarantineRequest,
)


def build_request() -> QuarantineRequest:
    return QuarantineRequest(
        stream_name="oryak:jobs",
        stream_entry_id="1000-0",
        message_digest="a" * 64,
        failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
        job_id=None,
        original_event_id=None,
        original_schema_version=None,
        trace_id=None,
        received_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_quarantine_commits_before_acknowledging_original_message() -> None:
    request = build_request()
    receipt = QuarantineReceipt(
        quarantine_id=uuid4(),
        dlq_event_id=uuid4(),
    )
    events: list[str] = []

    repository = SimpleNamespace(record=AsyncMock(side_effect=lambda _: (events.append("record") or receipt)))
    transaction = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: events.append("commit")),
        rollback=AsyncMock(side_effect=lambda: events.append("rollback")),
    )
    acknowledger = SimpleNamespace(acknowledge=AsyncMock(side_effect=lambda _: events.append("ack")))

    execution = QuarantineExecution(
        repository=repository,
        transaction=transaction,
        acknowledger=acknowledger,
    )

    result = await execution.execute(request)

    assert result == receipt
    assert events == ["record", "commit", "ack"]

    repository.record.assert_awaited_once_with(request)
    transaction.commit.assert_awaited_once()
    transaction.rollback.assert_not_awaited()
    acknowledger.acknowledge.assert_awaited_once_with(request.stream_entry_id)


@pytest.mark.asyncio
async def test_quarantine_record_failure_rolls_back_without_ack() -> None:
    request = build_request()

    repository = SimpleNamespace(record=AsyncMock(side_effect=RuntimeError("synthetic quarantine failure")))
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    acknowledger = SimpleNamespace(acknowledge=AsyncMock())

    execution = QuarantineExecution(
        repository=repository,
        transaction=transaction,
        acknowledger=acknowledger,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic quarantine failure",
    ):
        await execution.execute(request)

    transaction.commit.assert_not_awaited()
    transaction.rollback.assert_awaited_once()
    acknowledger.acknowledge.assert_not_awaited()


@pytest.mark.asyncio
async def test_quarantine_commit_failure_rolls_back_without_ack() -> None:
    request = build_request()
    receipt = QuarantineReceipt(
        quarantine_id=uuid4(),
        dlq_event_id=uuid4(),
    )

    repository = SimpleNamespace(record=AsyncMock(return_value=receipt))
    transaction = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("synthetic commit failure")),
        rollback=AsyncMock(),
    )
    acknowledger = SimpleNamespace(acknowledge=AsyncMock())

    execution = QuarantineExecution(
        repository=repository,
        transaction=transaction,
        acknowledger=acknowledger,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic commit failure",
    ):
        await execution.execute(request)

    transaction.rollback.assert_awaited_once()
    acknowledger.acknowledge.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_failure_does_not_roll_back_committed_quarantine() -> None:
    request = build_request()
    receipt = QuarantineReceipt(
        quarantine_id=uuid4(),
        dlq_event_id=uuid4(),
    )

    repository = SimpleNamespace(record=AsyncMock(return_value=receipt))
    transaction = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    acknowledger = SimpleNamespace(acknowledge=AsyncMock(side_effect=RuntimeError("synthetic ACK failure")))

    execution = QuarantineExecution(
        repository=repository,
        transaction=transaction,
        acknowledger=acknowledger,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic ACK failure",
    ):
        await execution.execute(request)

    transaction.commit.assert_awaited_once()
    transaction.rollback.assert_not_awaited()
    acknowledger.acknowledge.assert_awaited_once_with(request.stream_entry_id)
