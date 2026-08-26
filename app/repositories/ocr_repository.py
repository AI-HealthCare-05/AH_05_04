from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.medical_documents import MedicalDocument
from app.models.ocr import ConfirmationStatus, ExtractedField, OcrJob, OcrStatus


class OcrRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_job(self, *, document: MedicalDocument) -> OcrJob | None:
        result = await self.session.execute(
            select(OcrJob).where(
                OcrJob.document_id == document.id,
                OcrJob.ocr_status.in_([OcrStatus.PENDING, OcrStatus.PROCESSING]),
            )
        )
        return result.scalars().first()

    async def create_job(self, *, document: MedicalDocument) -> OcrJob:
        job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING)
        self.session.add(job)
        await self.session.flush()
        await self.session.refresh(
            job,
            attribute_names=["created_at"],
        )
        return job

    async def get_job_with_document(self, *, job_id: UUID) -> OcrJob | None:
        result = await self.session.execute(
            select(OcrJob)
            .options(selectinload(OcrJob.document), selectinload(OcrJob.extracted_fields))
            .where(OcrJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def replace_fields(self, *, ocr_job: OcrJob, fields: list[dict]) -> None:
        await self.session.execute(delete(ExtractedField).where(ExtractedField.ocr_job_id == ocr_job.id))
        for field in fields:
            self.session.add(ExtractedField(ocr_job_id=ocr_job.id, **field))
        await self.session.flush()

    async def get_field_owned(self, *, field_id: UUID, user_id: UUID) -> ExtractedField | None:
        result = await self.session.execute(
            select(ExtractedField)
            .options(selectinload(ExtractedField.ocr_job).selectinload(OcrJob.document))
            .where(ExtractedField.id == field_id)
        )
        field = result.scalar_one_or_none()
        if field is None or field.ocr_job.document.user_id != user_id:
            return None
        return field

    async def get_latest_completed_job(self, *, document: MedicalDocument) -> OcrJob | None:
        result = await self.session.execute(
            select(OcrJob)
            .options(selectinload(OcrJob.extracted_fields))
            .where(OcrJob.document_id == document.id, OcrJob.ocr_status == OcrStatus.COMPLETED)
            .order_by(OcrJob.created_at.desc(), OcrJob.created_sequence.desc())
        )
        return result.scalars().first()

    async def get_fields_for_job(self, *, ocr_job_id: UUID) -> list[ExtractedField]:
        result = await self.session.execute(select(ExtractedField).where(ExtractedField.ocr_job_id == ocr_job_id))
        return list(result.scalars().all())

    async def mark_processing(self, job: OcrJob, *, started_at: datetime) -> OcrJob:
        job.ocr_status = OcrStatus.PROCESSING
        job.started_at = started_at
        await self.session.flush()
        return job

    async def mark_completed(
        self,
        job: OcrJob,
        *,
        completed_at: datetime,
        engine_name: str | None,
        model_version: str | None,
        prompt_version: str | None,
    ) -> OcrJob:
        job.ocr_status = OcrStatus.COMPLETED
        job.completed_at = completed_at

        # 실제 OCR 및 구조화 실행 정보를 성공 작업에 기록합니다.
        job.engine_name = engine_name
        job.model_version = model_version
        job.prompt_version = prompt_version

        await self.session.flush()
        return job

    async def mark_failed(
        self,
        job: OcrJob,
        *,
        error_code: str,
        error_message: str,
        completed_at: datetime,
    ) -> OcrJob:
        job.ocr_status = OcrStatus.FAILED
        job.error_code = error_code
        job.error_message = error_message[:500]
        job.completed_at = completed_at
        # 이후 서비스 계층에서 ApiError를 다시 발생시키면 get_db_session의 예외 처리가
        # 세션 전체를 rollback합니다. flush만으로는 실패 상태가 사라지므로 즉시 commit합니다.
        await self.session.commit()
        return job

    async def confirm_field(
        self,
        field: ExtractedField,
        *,
        confirmed_value: str,
        confirmed_at: datetime,
    ) -> ExtractedField:
        field.confirmed_value = confirmed_value
        field.confirmation_status = ConfirmationStatus.CONFIRMED
        field.confirmed_at = confirmed_at
        await self.session.flush()
        return field
