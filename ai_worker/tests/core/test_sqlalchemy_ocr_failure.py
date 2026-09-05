from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_ocr_failure import mark_linked_ocr_job_failed
from ai_worker.core.retry import FailureCode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_code", "expected_ocr_error_code"),
    [
        ("TIMEOUT", "OCR_PROVIDER_TIMEOUT"),
        ("DEPENDENCY_UNAVAILABLE", "OCR_PROVIDER_UNAVAILABLE"),
        ("INVALID_INPUT", "OCR_PROCESSING_FAILED"),
        ("UNSUPPORTED_SCHEMA", "OCR_PROCESSING_FAILED"),
        ("SAFETY_VALIDATION_FAILED", "OCR_PROCESSING_FAILED"),
        ("RETRY_EXHAUSTED", "OCR_PROCESSING_FAILED"),
        ("INTERNAL_ERROR", "OCR_PROCESSING_FAILED"),
    ],
)
async def test_worker_failure_maps_to_safe_ocr_terminal_error(
    failure_code: FailureCode,
    expected_ocr_error_code: str,
) -> None:
    completed_at = datetime.now(UTC)
    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = "ocr-job-id"
    session.execute.return_value = update_result

    updated = await mark_linked_ocr_job_failed(
        session,
        ai_job_id="ai-job-id",
        failure_code=failure_code,
        completed_at=completed_at,
    )

    assert updated is True
    statement = session.execute.await_args.args[0]
    statement_sql = str(statement)
    parameters = statement.compile().params

    assert "UPDATE ocr_job SET" in statement_sql
    assert "ocr_job.ai_job_id" in statement_sql
    assert "ocr_job.ocr_status" in statement_sql
    assert "FAILED" in parameters.values()
    assert expected_ocr_error_code in parameters.values()
    assert completed_at in parameters.values()
    assert all("Provider" not in str(value) for value in parameters.values())
