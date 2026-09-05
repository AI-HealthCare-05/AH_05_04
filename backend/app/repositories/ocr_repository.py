from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.medical_documents import MedicalDocument
from app.models.ocr import ConfirmationStatus, ExtractedField, OcrJob, OcrStatus
from app.repositories.profile_ownership import owned_by_self


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

    async def create_job(self, *, document: MedicalDocument, ai_job_id: UUID | None = None) -> OcrJob:
        job = OcrJob(document_id=document.id, ai_job_id=ai_job_id, ocr_status=OcrStatus.PENDING)
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

    async def get_by_ai_job_id(self, *, ai_job_id: UUID) -> OcrJob | None:
        """#212가 추가한 `ocr_job.ai_job_id`(unique) 영속 매핑으로 조회합니다. Outbox 기반
        임시 조회(`AsyncJobRepository.get_interim_domain_reference`)와 달리 Outbox 30일
        보존과 무관하게 Job 90일 보존 동안 유지됩니다 — rediscovery·`GET /jobs/{job_id}`가
        이 값이 채워진 뒤에는 이 경로를 우선 사용해야 합니다."""
        result = await self.session.execute(select(OcrJob).where(OcrJob.ai_job_id == ai_job_id))
        return result.scalar_one_or_none()

    async def get_job_owned(self, *, job_id: UUID, user_id: UUID) -> OcrJob | None:
        result = await self.session.execute(
            select(OcrJob)
            .join(MedicalDocument, MedicalDocument.id == OcrJob.document_id)
            .options(selectinload(OcrJob.document), selectinload(OcrJob.extracted_fields))
            .where(
                OcrJob.id == job_id,
                owned_by_self(MedicalDocument.profile_id, user_id),
            )
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
            .join(OcrJob, OcrJob.id == ExtractedField.ocr_job_id)
            .join(MedicalDocument, MedicalDocument.id == OcrJob.document_id)
            .options(
                # PATCH 전에 문서 소유권을 확인하고 lock 대상 document_id를 얻기 위해
                # OCR 작업 관계만 eager loading 합니다.
                # 확정 처방 여부는 문서 row를 잠근 뒤 별도로 조회하므로 여기서 미리 읽지 않습니다.
                selectinload(ExtractedField.ocr_job).selectinload(OcrJob.document)
            )
            .where(
                ExtractedField.id == field_id,
                # 다른 사용자의 필드는 존재 여부가 노출되지 않도록 조회 자체에서 걸러집니다.
                owned_by_self(MedicalDocument.profile_id, user_id),
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_job_for_document_owned(self, *, document_id: UUID, user_id: UUID) -> OcrJob | None:
        """async-job-v1.md "공통 화면 재접속 복구": 화면 재진입 시 새 Job을 만들지 않고 기존 Job의
        polling을 재개하기 위해, 이 문서의 가장 최근 OCR Job 하나만 돌려줍니다. `id`는 무작위 UUID라
        같은 transaction 안에서 `created_at`이 동일할 때(Postgres `now()`는 transaction 시작
        시각) 정렬 기준이 될 수 없으므로, `get_latest_completed_job`과 같이 `created_sequence`
        (`idx_ocr_document_created_seq`)로 타이브레이크합니다."""
        result = await self.session.execute(
            select(OcrJob)
            .join(MedicalDocument, MedicalDocument.id == OcrJob.document_id)
            .where(
                OcrJob.document_id == document_id,
                owned_by_self(MedicalDocument.profile_id, user_id),
            )
            .order_by(OcrJob.created_at.desc(), OcrJob.created_sequence.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

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
        confirmed_value: str | None,
        confirmed_at: datetime,
    ) -> ExtractedField:
        field.confirmed_value = confirmed_value
        field.confirmation_status = ConfirmationStatus.CONFIRMED
        field.confirmed_at = confirmed_at
        await self.session.flush()
        return field
