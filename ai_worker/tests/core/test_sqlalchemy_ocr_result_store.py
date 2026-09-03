"""SQLAlchemy OCR 결과 저장소의 transaction 경계 테스트입니다."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_ocr_result_store import (
    SqlAlchemyOcrResultStore,
)
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tasks.ocr.handler import (
    OcrHandlerSuccess,
    OcrRecognizedField,
)


def build_message() -> WorkerMessage:
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
            "attempt": 1,
            "available_at": now.isoformat(),
            "enqueued_at": now.isoformat(),
            "trace_id": uuid4().hex,
        }
    )


@pytest.mark.asyncio
async def test_ocr_result_is_staged_without_commit() -> None:
    message = build_message()
    completed_at = datetime.now(UTC)
    result = OcrHandlerSuccess(
        event_id=message.event_id,
        job_id=message.job_id,
        handler_type=JobType.OCR,
        domain_id=message.domain_id,
        fields=(
            OcrRecognizedField(
                medication_index=1,
                field_type="MEDICATION_NAME",
                raw_value="합성 의약품",
                confidence_score=0.98,
                normalized_value=None,
                normalization_version=None,
            ),
        ),
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )

    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = str(message.domain_id)
    session.execute.side_effect = [
        update_result,
        MagicMock(),
        MagicMock(),
    ]
    store = SqlAlchemyOcrResultStore(
        session,
        clock=lambda: completed_at,
    )

    await store.save(
        message=message,
        result=result,
    )

    assert session.execute.await_count == 3

    statements = [call.args[0] for call in session.execute.await_args_list]
    update_sql = str(statements[0])
    delete_sql = str(statements[1])
    insert_sql = str(statements[2])
    update_where_sql = update_sql.split("WHERE", maxsplit=1)[1]

    assert update_sql.startswith("UPDATE ocr_job SET")
    assert "ocr_job.id" in update_where_sql
    assert "ocr_job.ai_job_id" in update_where_sql
    assert "ocr_job.ocr_status" in update_where_sql
    assert delete_sql.startswith("DELETE FROM extracted_field")
    assert "extracted_field.ocr_job_id" in delete_sql
    assert insert_sql.startswith("INSERT INTO extracted_field")
    assert "ocr_job_id" in insert_sql
    assert "field_type" in insert_sql
    assert "confirmation_status" in insert_sql

    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_mismatched_ocr_result_is_rejected_before_write() -> None:
    message = build_message()
    result = OcrHandlerSuccess(
        event_id=message.event_id,
        job_id=message.job_id,
        handler_type=JobType.OCR,
        domain_id=uuid4(),
        fields=(),
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )
    session = AsyncMock(spec=AsyncSession)
    store = SqlAlchemyOcrResultStore(
        session,
        clock=lambda: datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="OCR 결과 식별자가 일치하지 않습니다"):
        await store.save(
            message=message,
            result=result,
        )

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_ocr_result_clears_existing_fields_without_insert() -> None:
    message = build_message()
    result = OcrHandlerSuccess(
        event_id=message.event_id,
        job_id=message.job_id,
        handler_type=JobType.OCR,
        domain_id=message.domain_id,
        fields=(),
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )
    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = str(message.domain_id)
    session.execute.side_effect = [
        update_result,
        MagicMock(),
    ]
    store = SqlAlchemyOcrResultStore(
        session,
        clock=lambda: datetime.now(UTC),
    )

    await store.save(
        message=message,
        result=result,
    )

    assert session.execute.await_count == 2
    statements = [call.args[0] for call in session.execute.await_args_list]

    assert str(statements[0]).startswith("UPDATE ocr_job SET")
    assert str(statements[1]).startswith("DELETE FROM extracted_field")
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unwritable_ocr_job_does_not_replace_fields() -> None:
    message = build_message()
    result = OcrHandlerSuccess(
        event_id=message.event_id,
        job_id=message.job_id,
        handler_type=JobType.OCR,
        domain_id=message.domain_id,
        fields=(
            OcrRecognizedField(
                medication_index=1,
                field_type="MEDICATION_NAME",
                raw_value="합성 의약품",
                confidence_score=0.98,
                normalized_value=None,
                normalization_version=None,
            ),
        ),
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )
    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = None
    session.execute.return_value = update_result
    store = SqlAlchemyOcrResultStore(
        session,
        clock=lambda: datetime.now(UTC),
    )

    with pytest.raises(
        ValueError,
        match="저장 가능한 OCR Job을 찾을 수 없습니다",
    ):
        await store.save(
            message=message,
            result=result,
        )

    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
