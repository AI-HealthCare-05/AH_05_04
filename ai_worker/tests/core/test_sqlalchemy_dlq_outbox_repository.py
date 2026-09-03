"""SQLAlchemy DLQ Outbox Repository 테스트입니다."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from ai_worker.adapters.sqlalchemy_dlq_outbox_repository import (
    DlqOutboxStateError,
    SqlAlchemyDlqOutboxRepository,
)
from ai_worker.core.quarantine import QuarantineFailureCode


def compiled_sql(statement: object) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_claim_next_uses_skip_locked_and_increments_attempt() -> None:
    now = datetime.now(UTC)
    claim_expires_at = now + timedelta(seconds=30)
    event_id = uuid4()
    quarantine_id = uuid4()

    candidate_result = MagicMock()
    candidate_result.mappings.return_value.one_or_none.return_value = {
        "event_id": str(event_id),
        "quarantine_id": str(quarantine_id),
        "attempt_count": 0,
        "original_schema_version": "2.0",
        "stream_entry_id": "1000-0",
        "message_digest": "a" * 64,
        "failure_code": "INVALID_MESSAGE_SCHEMA",
        "trace_id": "b" * 32,
    }

    claim_result = MagicMock()
    claim_result.scalar_one_or_none.return_value = 1

    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            candidate_result,
            claim_result,
        ]
    )

    repository = SqlAlchemyDlqOutboxRepository(session)

    claimed = await repository.claim_next(
        now=now,
        claim_expires_at=claim_expires_at,
    )

    assert claimed is not None
    assert claimed.envelope.event_id == event_id
    assert claimed.envelope.quarantine_id == quarantine_id
    assert claimed.envelope.stream_entry_id == "1000-0"
    assert claimed.envelope.message_digest == "a" * 64
    assert claimed.envelope.failure_code is QuarantineFailureCode.INVALID_MESSAGE_SCHEMA
    assert claimed.envelope.original_schema_version == "2.0"
    assert claimed.envelope.trace_id == "b" * 32
    assert claimed.attempt_count == 1
    assert len(claimed.claim_token) == 32

    select_statement = session.execute.await_args_list[0].args[0]
    select_sql = compiled_sql(select_statement)

    assert "FROM dlq_outbox_event JOIN message_quarantine" in select_sql
    assert "FOR UPDATE OF dlq_outbox_event SKIP LOCKED" in select_sql
    assert "dlq_outbox_event.available_at <=" in select_sql
    assert "dlq_outbox_event.claim_expires_at <=" in select_sql

    claim_statement = session.execute.await_args_list[1].args[0]
    claim_sql = compiled_sql(claim_statement)
    claim_params = claim_statement.compile(dialect=postgresql.dialect()).params

    assert "UPDATE dlq_outbox_event" in claim_sql
    assert "attempt_count=(dlq_outbox_event.attempt_count + 1)" in (claim_sql.replace("\n", ""))
    assert "CLAIMED" in claim_params.values()
    assert str(event_id) in claim_params.values()
    assert claimed.claim_token in claim_params.values()
    assert claim_expires_at in claim_params.values()


@pytest.mark.asyncio
async def test_claim_next_returns_none_when_no_event_is_due() -> None:
    candidate_result = MagicMock()
    candidate_result.mappings.return_value.one_or_none.return_value = None

    session = MagicMock()
    session.execute = AsyncMock(return_value=candidate_result)

    repository = SqlAlchemyDlqOutboxRepository(session)
    now = datetime.now(UTC)

    claimed = await repository.claim_next(
        now=now,
        claim_expires_at=now + timedelta(seconds=30),
    )

    assert claimed is None
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_mark_published_uses_claim_token_fencing() -> None:
    event_id = uuid4()
    published_at = datetime.now(UTC)
    claim_token = uuid4().hex

    result = MagicMock()
    result.rowcount = 1

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    repository = SqlAlchemyDlqOutboxRepository(session)

    await repository.mark_published(
        event_id=event_id,
        claim_token=claim_token,
        published_at=published_at,
    )

    statement = session.execute.await_args.args[0]
    sql = compiled_sql(statement)
    params = statement.compile(dialect=postgresql.dialect()).params

    assert "UPDATE dlq_outbox_event" in sql
    assert "dlq_outbox_event.claim_token =" in sql
    assert "dlq_outbox_event.status =" in sql
    assert "PUBLISHED" in params.values()
    assert "CLAIMED" in params.values()
    assert str(event_id) in params.values()
    assert claim_token in params.values()
    assert published_at in params.values()


@pytest.mark.asyncio
async def test_mark_published_rejects_stale_claim_token() -> None:
    result = MagicMock()
    result.rowcount = 0

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    repository = SqlAlchemyDlqOutboxRepository(session)

    with pytest.raises(DlqOutboxStateError):
        await repository.mark_published(
            event_id=uuid4(),
            claim_token=uuid4().hex,
            published_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_reschedule_keeps_event_id_and_releases_claim() -> None:
    event_id = uuid4()
    claim_token = uuid4().hex
    available_at = datetime.now(UTC) + timedelta(seconds=5)

    result = MagicMock()
    result.rowcount = 1

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    repository = SqlAlchemyDlqOutboxRepository(session)

    await repository.reschedule(
        event_id=event_id,
        claim_token=claim_token,
        available_at=available_at,
        error_code="DLQ_PUBLISH_FAILED",
    )

    statement = session.execute.await_args.args[0]
    sql = compiled_sql(statement)
    params = statement.compile(dialect=postgresql.dialect()).params

    assert "UPDATE dlq_outbox_event" in sql
    assert "dlq_outbox_event.claim_token =" in sql
    assert str(event_id) in params.values()
    assert claim_token in params.values()
    assert "PENDING" in params.values()
    assert "CLAIMED" in params.values()
    assert "DLQ_PUBLISH_FAILED" in params.values()
    assert available_at in params.values()
