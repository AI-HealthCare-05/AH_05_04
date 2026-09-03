"""SQLAlchemy 기반 OCR Worker 입력 Repository입니다."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import DateTime, String, column, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.ocr.handler import OcrDomainInput


class ProcessingClock(Protocol):
    def __call__(self) -> datetime:
        """timezone-aware 현재 시각을 반환합니다."""
        ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


_OCR_JOB = table(
    "ocr_job",
    column("id", String(36)),
    column("document_id", String(36)),
    column("ai_job_id", String(36)),
    column("ocr_status", String(20)),
    column("started_at", DateTime(timezone=True)),
)

_MEDICAL_DOCUMENT = table(
    "medical_document",
    column("id", String(36)),
    column("object_key", String(500)),
    column("file_mime_type", String(100)),
)


class SqlAlchemyOcrInputRepository:
    """현재 AI Job과 연결된 OCR 도메인 입력을 조회합니다.

    주입된 session을 사용하며 transaction을 직접 commit하지 않습니다.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: ProcessingClock = _utc_now,
    ) -> None:
        self._session = session
        self._clock = clock

    async def get_input(
        self,
        *,
        domain_id: UUID,
        job_id: UUID,
    ) -> OcrDomainInput | None:
        """OCR Job과 AI Job 연결이 모두 일치할 때만 입력을 반환합니다."""

        statement = (
            select(
                _MEDICAL_DOCUMENT.c.object_key,
                _MEDICAL_DOCUMENT.c.file_mime_type,
            )
            .select_from(
                _OCR_JOB.join(
                    _MEDICAL_DOCUMENT,
                    _OCR_JOB.c.document_id == _MEDICAL_DOCUMENT.c.id,
                )
            )
            .where(
                _OCR_JOB.c.id == str(domain_id),
                _OCR_JOB.c.ai_job_id == str(job_id),
            )
        )

        result = await self._session.execute(statement)
        row = result.one_or_none()

        if row is None:
            return None

        return OcrDomainInput(
            object_key=row.object_key,
            file_mime_type=row.file_mime_type,
        )

    async def mark_processing(
        self,
        *,
        domain_id: UUID,
        job_id: UUID,
    ) -> bool:
        """연결된 PENDING OCR Job에 시작 시각을 기록합니다."""

        statement = (
            update(_OCR_JOB)
            .where(
                _OCR_JOB.c.id == str(domain_id),
                _OCR_JOB.c.ai_job_id == str(job_id),
                _OCR_JOB.c.ocr_status == "PENDING",
            )
            .values(
                ocr_status="PROCESSING",
                started_at=self._clock(),
            )
            .returning(_OCR_JOB.c.id)
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None
