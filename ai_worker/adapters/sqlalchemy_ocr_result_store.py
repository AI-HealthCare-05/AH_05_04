"""SQLAlchemy 기반 OCR Worker 결과 저장소입니다."""

from datetime import datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    String,
    column,
    delete,
    insert,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core.results import HandlerSuccess
from ai_worker.schemas.messages import JobType, WorkerMessage
from ai_worker.tasks.ocr.handler import OcrHandlerSuccess

_OCR_JOB = table(
    "ocr_job",
    column("id", String(36)),
    column("ai_job_id", String(36)),
    column("ocr_status", String(20)),
    column("engine_name", String(100)),
    column("model_version", String(100)),
    column("prompt_version", String(100)),
    column("completed_at", DateTime(timezone=True)),
    column("error_code", String(100)),
    column("error_message", String(500)),
)

_EXTRACTED_FIELD = table(
    "extracted_field",
    column("id", String(36)),
    column("ocr_job_id", String(36)),
    column("medication_index", Integer),
    column("field_type", String(30)),
    column("raw_value", String(1000)),
    column("confidence_score", Numeric(5, 4)),
    column("normalized_value", String(1000)),
    column("normalization_version", String(30)),
    column("confirmed_value", String(1000)),
    column("confirmation_status", String(20)),
    column("confirmed_at", DateTime(timezone=True)),
)


class CompletionClock(Protocol):
    """결과 완료 시각을 주입하기 위한 clock 계약입니다."""

    def __call__(self) -> datetime:
        """timezone-aware 완료 시각을 반환합니다."""
        ...


class SqlAlchemyOcrResultStore:
    """OCR 결과를 현재 session transaction에 적재합니다.

    commit과 rollback은 #141 Consumer 실행 계층이 담당합니다.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: CompletionClock,
    ) -> None:
        self._session = session
        self._clock = clock

    async def save(
        self,
        *,
        message: WorkerMessage,
        result: HandlerSuccess,
    ) -> None:
        """검증된 OCR 결과를 적재하되 직접 commit하지 않습니다."""

        if not isinstance(result, OcrHandlerSuccess):
            raise ValueError("OCR Handler 결과가 아닙니다.")

        if (
            result.event_id != message.event_id
            or result.job_id != message.job_id
            or result.handler_type is not JobType.OCR
            or result.domain_id != message.domain_id
        ):
            raise ValueError("OCR 결과 식별자가 일치하지 않습니다.")

        completed_at = self._clock()

        update_statement = (
            update(_OCR_JOB)
            .where(
                _OCR_JOB.c.id == str(message.domain_id),
                _OCR_JOB.c.ai_job_id == str(message.job_id),
                _OCR_JOB.c.ocr_status.in_(("PENDING", "PROCESSING")),
            )
            .values(
                ocr_status="COMPLETED",
                engine_name=result.engine_name,
                model_version=result.model_version,
                prompt_version=result.prompt_version,
                completed_at=completed_at,
                error_code=None,
                error_message=None,
            )
            .returning(_OCR_JOB.c.id)
        )
        update_result = await self._session.execute(update_statement)

        if update_result.scalar_one_or_none() is None:
            raise ValueError("저장 가능한 OCR Job을 찾을 수 없습니다.")

        await self._session.execute(
            delete(_EXTRACTED_FIELD).where(
                _EXTRACTED_FIELD.c.ocr_job_id == str(message.domain_id),
            )
        )

        if not result.fields:
            return

        field_rows = [
            {
                "id": str(uuid4()),
                "ocr_job_id": str(message.domain_id),
                "medication_index": field.medication_index,
                "field_type": field.field_type,
                "raw_value": field.raw_value,
                "confidence_score": field.confidence_score,
                "normalized_value": field.normalized_value,
                "normalization_version": field.normalization_version,
                "confirmed_value": None,
                "confirmation_status": "UNCONFIRMED",
                "confirmed_at": None,
            }
            for field in result.fields
        ]

        await self._session.execute(
            insert(_EXTRACTED_FIELD),
            field_rows,
        )
