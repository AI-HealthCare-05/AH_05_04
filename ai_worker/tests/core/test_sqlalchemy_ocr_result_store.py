"""SQLAlchemy OCR 결과 저장소의 transaction 경계 테스트입니다."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_ocr_result_store import (
    SqlAlchemyOcrResultStore,
    _fill_missing_required_fields,
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
    assert "ocr_job.ocr_status =" in update_where_sql
    assert "ocr_job.ocr_status IN" not in update_where_sql

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
async def test_empty_ocr_result_still_inserts_prescribed_date_placeholder() -> None:
    """#294: OCR이 아무 필드도 인식하지 못해도(빈 결과) PRESCRIBED_DATE만은 항상
    raw_value=null placeholder row로 채워야 사용자가 검수 화면에서 직접 입력할 수 있다.
    이전에는 빈 결과에서 DELETE만 하고 아무것도 INSERT하지 않아, 검수할 row 자체가
    없어 처방 확정이 영구히 막혔다."""
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

    assert session.execute.await_count == 3
    statements = [call.args[0] for call in session.execute.await_args_list]
    insert_call = session.execute.await_args_list[2]

    assert str(statements[0]).startswith("UPDATE ocr_job SET")
    assert str(statements[1]).startswith("DELETE FROM extracted_field")
    assert str(statements[2]).startswith("INSERT INTO extracted_field")

    inserted_rows = insert_call.args[1]
    assert len(inserted_rows) == 1
    placeholder = inserted_rows[0]
    assert placeholder["medication_index"] == 0
    assert placeholder["field_type"] == "PRESCRIBED_DATE"
    assert placeholder["raw_value"] is None
    assert placeholder["confirmation_status"] == "UNCONFIRMED"
    assert placeholder["confirmed_value"] is None
    assert placeholder["confirmed_at"] is None

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


@pytest.mark.asyncio
async def test_missing_medication_fields_are_backfilled_only_for_detected_index() -> None:
    """이미 감지된 medication_index(=1)에 대해서만 누락된 필수 필드(DOSE_VALUE 등)를
    채운다. 선택 필드(MEDICATION_STRENGTH 등)는 채우지 않는다."""
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
            OcrRecognizedField(
                medication_index=0,
                field_type="PRESCRIBED_DATE",
                raw_value="2026-09-01",
                confidence_score=0.95,
                normalized_value="2026-09-01",
                normalization_version="v1",
            ),
        ),
        engine_name="CLOVA_OCR",
        model_version=None,
        prompt_version=None,
    )
    session = AsyncMock(spec=AsyncSession)
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = str(message.domain_id)
    session.execute.side_effect = [update_result, MagicMock(), MagicMock()]
    store = SqlAlchemyOcrResultStore(session, clock=lambda: datetime.now(UTC))

    await store.save(message=message, result=result)

    inserted_rows = session.execute.await_args_list[2].args[1]
    keys = {(row["medication_index"], row["field_type"]) for row in inserted_rows}

    assert keys == {
        (0, "PRESCRIBED_DATE"),
        (1, "MEDICATION_NAME"),
        (1, "DOSE_VALUE"),
        (1, "FREQUENCY_PER_DAY"),
        (1, "DURATION_DAYS"),
    }
    original_prescribed_date = next(row for row in inserted_rows if row["field_type"] == "PRESCRIBED_DATE")
    assert original_prescribed_date["raw_value"] == "2026-09-01"  # 원본 값이 placeholder로 덮이지 않는다.
    dose_value_placeholder = next(row for row in inserted_rows if row["field_type"] == "DOSE_VALUE")
    assert dose_value_placeholder["raw_value"] is None
    assert dose_value_placeholder["confirmation_status"] == "UNCONFIRMED"


def _row(medication_index: int, field_type: str, raw_value: str | None = "값") -> dict:
    return {
        "id": "row-id",
        "ocr_job_id": "job-id",
        "medication_index": medication_index,
        "field_type": field_type,
        "raw_value": raw_value,
        "confidence_score": None,
        "normalized_value": None,
        "normalization_version": None,
        "confirmed_value": None,
        "confirmation_status": "UNCONFIRMED",
        "confirmed_at": None,
    }


def test_fill_missing_required_fields_does_not_create_medication_index_when_none_detected() -> None:
    """medication이 하나도 감지되지 않으면(빈 리스트) PRESCRIBED_DATE만 채우고 새
    medication index는 만들지 않는다."""
    filled = _fill_missing_required_fields([], ocr_job_id="job-id")

    assert len(filled) == 1
    assert filled[0]["medication_index"] == 0
    assert filled[0]["field_type"] == "PRESCRIBED_DATE"


def test_fill_missing_required_fields_does_not_duplicate_existing_rows() -> None:
    rows = [
        _row(0, "PRESCRIBED_DATE"),
        _row(1, "MEDICATION_NAME"),
        _row(1, "DOSE_VALUE"),
        _row(1, "FREQUENCY_PER_DAY"),
        _row(1, "DURATION_DAYS"),
    ]

    filled = _fill_missing_required_fields(rows, ocr_job_id="job-id")

    assert filled == rows


def test_fill_missing_required_fields_does_not_touch_a_complete_medication_when_another_has_a_gap() -> None:
    """medication 2곳이 감지됐고 그중 하나(index 1)는 이미 필수 필드가 전부 있으면, 그
    medication에는 어떤 row도 추가되지 않는다 — 다른 medication(index 2)의 누락만 채운다."""
    rows = [
        _row(0, "PRESCRIBED_DATE"),
        _row(1, "MEDICATION_NAME"),
        _row(1, "DOSE_VALUE"),
        _row(1, "FREQUENCY_PER_DAY"),
        _row(1, "DURATION_DAYS"),
        _row(2, "MEDICATION_NAME"),
        # index 2는 DOSE_VALUE/FREQUENCY_PER_DAY/DURATION_DAYS가 누락됐다.
    ]

    filled = _fill_missing_required_fields(rows, ocr_job_id="job-id")

    index_1_rows = [row for row in filled if row["medication_index"] == 1]
    index_2_rows = {row["field_type"] for row in filled if row["medication_index"] == 2}

    assert index_1_rows == [row for row in rows if row["medication_index"] == 1]  # 그대로, 추가 없음
    assert index_2_rows == {"MEDICATION_NAME", "DOSE_VALUE", "FREQUENCY_PER_DAY", "DURATION_DAYS"}
