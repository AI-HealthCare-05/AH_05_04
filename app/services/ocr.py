from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.dtos.ocr import ExecuteOcrRequest, ExtractedFieldData, OcrJobData, OcrJobStatus
from app.dtos.prescriptions import UpdateExtractedFieldRequest
from app.models.ocr import ExtractedField, OcrJob
from app.models.users import User
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.ocr_repository import OcrRepository
from app.services.ocr_engine import NotConfiguredOcrEngine, OcrEngine, OcrProcessingError, OcrProviderUnavailableError


def _to_field_data(field: ExtractedField) -> ExtractedFieldData:
    return ExtractedFieldData(
        field_id=field.id,
        field_type=str(field.field_type),
        medication_index=field.medication_index,
        raw_value=field.raw_value,
        confirmed_value=field.confirmed_value,
        confidence_score=float(field.confidence_score) if field.confidence_score is not None else None,
        confirmation_status=str(field.confirmation_status),
    )


def _to_job_data(job: OcrJob, fields: list[ExtractedField]) -> OcrJobData:
    return OcrJobData(
        job_id=job.id,
        document_id=job.document_id,
        ocr_status=OcrJobStatus(job.ocr_status),
        error_code=job.error_code,
        created_at=job.created_at,
        completed_at=job.completed_at,
        fields=[_to_field_data(field) for field in fields],
    )


class OcrService:
    def __init__(
        self,
        document_repository: MedicalDocumentRepository,
        ocr_repository: OcrRepository,
        engine: OcrEngine | None = None,
    ) -> None:
        self._engine: OcrEngine = engine or NotConfiguredOcrEngine()
        self._document_repo = document_repository
        self._ocr_repo = ocr_repository

    async def execute_ocr(
        self,
        *,
        user: User,
        document_id: UUID,
        request: ExecuteOcrRequest,
    ) -> OcrJobData:
        # OCR 실행 Backend 계약: 문서 소유권 확인 후 OCR 작업을 생성하고 같은 요청 안에서 처리합니다.
        document = await self._document_repo.get_owned(document_id=document_id, user=user)
        if document is None:
            raise ApiError(
                status_code=404,
                code="MEDICAL_DOCUMENT_NOT_FOUND",
                message="의료문서를 찾을 수 없습니다.",
                details=[ErrorDetail(field="document_id", reason="NOT_FOUND", rejected_value=str(document_id))],
            )

        if not request.force_reprocess:
            active_job = await self._ocr_repo.get_active_job(document=document)
            if active_job is not None:
                raise ApiError(
                    status_code=409,
                    code="OCR_JOB_ALREADY_PROCESSING",
                    message="이미 OCR 처리가 진행 중입니다.",
                    details=[ErrorDetail(field="document_id", reason="OCR_JOB_IN_PROGRESS")],
                )

        job = await self._ocr_repo.create_job(document=document)
        job = await self._ocr_repo.mark_processing(job, started_at=datetime.now(UTC))

        try:
            result = await self._engine.recognize(
                object_key=document.object_key,
                file_mime_type=document.file_mime_type,
            )
        except OcrProviderUnavailableError as err:
            await self._ocr_repo.mark_failed(
                job,
                error_code="PROVIDER_UNAVAILABLE",
                error_message=str(err),
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=503,
                code="OCR_PROVIDER_UNAVAILABLE",
                message="OCR 서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="provider", reason="PROVIDER_UNAVAILABLE")],
            ) from err
        except OcrProcessingError as err:
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_ENGINE_ERROR",
                error_message=str(err),
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=500,
                code="OCR_PROCESSING_FAILED",
                message="처방전 인식에 실패했습니다. 다시 시도하거나 직접 입력해 주세요.",
                details=[ErrorDetail(field="ocr", reason="OCR_ENGINE_ERROR")],
            ) from err
        except Exception as err:
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_ENGINE_ERROR",
                error_message=str(err),
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=500,
                code="OCR_PROCESSING_FAILED",
                message="처방전 인식에 실패했습니다. 다시 시도하거나 직접 입력해 주세요.",
                details=[ErrorDetail(field="ocr", reason="OCR_ENGINE_ERROR")],
            ) from err

        await self._ocr_repo.replace_fields(
            ocr_job=job,
            fields=[
                {
                    "medication_index": field.medication_index,
                    "field_type": field.field_type,
                    "raw_value": field.raw_value,
                    "confidence_score": field.confidence_score,
                }
                for field in result.fields
            ],
        )

        job = await self._ocr_repo.mark_completed(job, completed_at=datetime.now(UTC))

        saved_fields = await self._ocr_repo.get_fields_for_job(ocr_job_id=job.id)
        return _to_job_data(job, saved_fields)

    async def get_ocr_job_result(self, *, user: User, job_id: UUID) -> OcrJobData:
        job = await self._ocr_repo.get_job_with_document(job_id=job_id)
        if job is None or job.document.user_id != user.id:
            raise ApiError(
                status_code=404,
                code="OCR_JOB_NOT_FOUND",
                message="OCR 작업 정보를 찾을 수 없습니다.",
                details=[ErrorDetail(field="job_id", reason="NOT_FOUND", rejected_value=str(job_id))],
            )
        return _to_job_data(job, list(job.extracted_fields))

    async def update_extracted_field(
        self,
        *,
        user: User,
        field_id: UUID,
        request: UpdateExtractedFieldRequest,
    ) -> ExtractedFieldData:
        # OCR 추출 필드 확인/수정 Backend 계약: 사용자가 인식 결과를 검토·수정합니다.
        field = await self._ocr_repo.get_field_owned(field_id=field_id, user_id=user.id)
        if field is None:
            raise ApiError(
                status_code=404,
                code="EXTRACTED_FIELD_NOT_FOUND",
                message="추출 필드를 찾을 수 없습니다.",
                details=[ErrorDetail(field="field_id", reason="NOT_FOUND", rejected_value=str(field_id))],
            )

        field = await self._ocr_repo.confirm_field(
            field,
            confirmed_value=request.confirmed_value,
            confirmed_at=datetime.now(UTC),
        )
        return _to_field_data(field)
