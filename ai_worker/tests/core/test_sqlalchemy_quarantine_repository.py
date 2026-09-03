from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_quarantine_repository import (
    SqlAlchemyQuarantineRepository,
)
from ai_worker.core.quarantine import (
    QuarantineFailureCode,
    QuarantineRequest,
)


def build_request(*, job_id=None) -> QuarantineRequest:
    return QuarantineRequest(
        stream_name="oryak:jobs",
        stream_entry_id="1000-0",
        message_digest="a" * 64,
        failure_code=QuarantineFailureCode.INVALID_MESSAGE_SCHEMA,
        job_id=job_id,
        original_event_id=uuid4(),
        original_schema_version="1.0",
        trace_id=uuid4().hex,
        received_at=datetime.now(UTC),
    )


def compiled_sql(statement: object) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
        )
    )


@pytest.mark.asyncio
async def test_record_creates_quarantine_and_dlq_outbox() -> None:
    job_id = uuid4()
    quarantine_id = uuid4()
    dlq_event_id = uuid4()
    request = build_request(job_id=job_id)

    session = AsyncMock(spec=AsyncSession)

    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = str(job_id)

    quarantine_result = MagicMock()
    quarantine_result.scalar_one_or_none.return_value = str(quarantine_id)

    dlq_result = MagicMock()
    dlq_result.scalar_one_or_none.return_value = str(dlq_event_id)

    session.execute.side_effect = [
        job_result,
        quarantine_result,
        dlq_result,
    ]

    repository = SqlAlchemyQuarantineRepository(session)

    receipt = await repository.record(request)

    assert receipt.quarantine_id == quarantine_id
    assert receipt.dlq_event_id == dlq_event_id
    assert session.execute.await_count == 3

    job_statement = session.execute.await_args_list[0].args[0]
    assert "FROM ai_job" in compiled_sql(job_statement)

    quarantine_statement = session.execute.await_args_list[1].args[0]
    quarantine_sql = compiled_sql(quarantine_statement)
    quarantine_params = quarantine_statement.compile(dialect=postgresql.dialect()).params

    assert "INSERT INTO message_quarantine" in quarantine_sql
    assert "ON CONFLICT" in quarantine_sql
    assert str(job_id) in quarantine_params.values()
    assert "oryak:jobs" in quarantine_params.values()
    assert "1000-0" in quarantine_params.values()
    assert "a" * 64 in quarantine_params.values()
    assert "INVALID_MESSAGE_SCHEMA" in {str(value) for value in quarantine_params.values()}

    dlq_statement = session.execute.await_args_list[2].args[0]
    dlq_sql = compiled_sql(dlq_statement)
    dlq_params = dlq_statement.compile(dialect=postgresql.dialect()).params

    assert "INSERT INTO dlq_outbox_event" in dlq_sql
    assert "ON CONFLICT" in dlq_sql
    assert str(quarantine_id) in dlq_params.values()
    assert "QUARANTINE_RECORDED" in dlq_params.values()
    assert "PENDING" in dlq_params.values()


@pytest.mark.asyncio
async def test_record_omits_unknown_job_reference() -> None:
    request = build_request(job_id=uuid4())
    quarantine_id = uuid4()
    dlq_event_id = uuid4()

    session = AsyncMock(spec=AsyncSession)

    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = None

    quarantine_result = MagicMock()
    quarantine_result.scalar_one_or_none.return_value = str(quarantine_id)

    dlq_result = MagicMock()
    dlq_result.scalar_one_or_none.return_value = str(dlq_event_id)

    session.execute.side_effect = [
        job_result,
        quarantine_result,
        dlq_result,
    ]

    repository = SqlAlchemyQuarantineRepository(session)

    await repository.record(request)

    quarantine_statement = session.execute.await_args_list[1].args[0]
    quarantine_params = quarantine_statement.compile(dialect=postgresql.dialect()).params

    job_id_params = [value for key, value in quarantine_params.items() if key.startswith("job_id")]

    assert job_id_params == [None]
