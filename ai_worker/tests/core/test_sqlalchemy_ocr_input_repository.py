"""SQLAlchemy OCR Worker 입력 Repository 단위 테스트입니다."""

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
