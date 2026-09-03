"""SQLAlchemy OCR Worker 입력 Repository 단위 테스트입니다."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_ocr_input_repository import (
    SqlAlchemyOcrInputRepository,
)
from ai_worker.tasks.ocr.handler import OcrDomainInput


@pytest.mark.asyncio
async def test_ocr_input_is_loaded_for_matching_domain_and_ai_job() -> None:
    domain_id = uuid4()
    job_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    query_result = MagicMock()
    query_result.one_or_none.return_value = SimpleNamespace(
        object_key="synthetic/input.png",
        file_mime_type="image/png",
    )
    session.execute.return_value = query_result
    repository = SqlAlchemyOcrInputRepository(session)

    result = await repository.get_input(
        domain_id=domain_id,
        job_id=job_id,
    )

    assert result == OcrDomainInput(
        object_key="synthetic/input.png",
        file_mime_type="image/png",
    )
    session.execute.assert_awaited_once()

    statement = session.execute.await_args.args[0]
    sql = str(statement)
    where_sql = sql.split("WHERE", maxsplit=1)[1]

    assert "FROM ocr_job JOIN medical_document" in sql
    assert "ocr_job.document_id = medical_document.id" in sql
    assert "ocr_job.id" in where_sql
    assert "ocr_job.ai_job_id" in where_sql


@pytest.mark.asyncio
async def test_missing_or_mismatched_ocr_input_returns_none() -> None:
    session = AsyncMock(spec=AsyncSession)
    query_result = MagicMock()
    query_result.one_or_none.return_value = None
    session.execute.return_value = query_result
    repository = SqlAlchemyOcrInputRepository(session)

    result = await repository.get_input(
        domain_id=uuid4(),
        job_id=uuid4(),
    )

    assert result is None
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_ocr_job_is_staged_as_processing() -> None:
    domain_id = uuid4()
    job_id = uuid4()
    started_at = datetime.now(UTC)
    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = str(domain_id)
    session.execute.return_value = update_result
    repository = SqlAlchemyOcrInputRepository(
        session,
        clock=lambda: started_at,
    )

    processing_started = await repository.mark_processing(
        domain_id=domain_id,
        job_id=job_id,
    )

    assert processing_started is True
    session.execute.assert_awaited_once()

    statement = session.execute.await_args.args[0]
    sql = str(statement)
    where_sql = sql.split("WHERE", maxsplit=1)[1]
    parameters = statement.compile().params

    assert sql.startswith("UPDATE ocr_job SET")
    assert "ocr_status" in sql
    assert "started_at" in sql
    assert "ocr_job.id" in where_sql
    assert "ocr_job.ai_job_id" in where_sql
    assert "ocr_job.ocr_status =" in where_sql
    assert "PROCESSING" in parameters.values()
    assert "PENDING" in parameters.values()
    assert started_at in parameters.values()

    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_pending_ocr_job_is_not_started() -> None:
    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = None
    session.execute.return_value = update_result
    repository = SqlAlchemyOcrInputRepository(
        session,
        clock=lambda: datetime.now(UTC),
    )

    processing_started = await repository.mark_processing(
        domain_id=uuid4(),
        job_id=uuid4(),
    )

    assert processing_started is False
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
