"""OCR 실행 시작 상태 전이 저장소 테스트입니다."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_ocr_execution_starter import (
    SqlAlchemyOcrExecutionStarter,
)
from ai_worker.schemas.messages import WorkerMessage


def build_message(*, attempt: int = 1) -> WorkerMessage:
    now = datetime.now(UTC)

    return WorkerMessage.model_validate(
        {
            "schema_version": "1.0",
            "event_id": str(uuid4()),
            "event_kind": "JOB_EXECUTE",
            "job_id": str(uuid4()),
            "job_type": "OCR",
            "domain_type": "OCR_JOB",
            "domain_id": str(uuid4()),
            "attempt": attempt,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": uuid4().hex,
        }
    )


@pytest.mark.asyncio
async def test_ocr_execution_is_staged_as_processing_without_commit() -> None:
    started_at = datetime.now(UTC)
    message = build_message()
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = str(message.domain_id)
    session.execute.return_value = result
    starter = SqlAlchemyOcrExecutionStarter(session)

    started = await starter.start(
        message=message,
        started_at=started_at,
    )

    assert started is True
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()

    statement = session.execute.await_args.args[0]
    sql = str(statement)
    where_sql = sql.partition(" WHERE ")[2]

    assert "UPDATE ocr_job SET" in sql
    assert "ocr_job.id" in where_sql
    assert "ocr_job.ai_job_id" in where_sql
    assert "ocr_job.ocr_status IN" in where_sql


@pytest.mark.asyncio
async def test_ocr_execution_start_rejects_mismatched_domain_or_state() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    starter = SqlAlchemyOcrExecutionStarter(session)

    started = await starter.start(
        message=build_message(attempt=2),
        started_at=datetime.now(UTC),
    )

    assert started is False
    session.commit.assert_not_awaited()
