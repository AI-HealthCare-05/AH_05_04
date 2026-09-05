from datetime import UTC, datetime
from uuid import UUID

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.dtos.ocr import ExecuteOcrRequest, ExtractedFieldData, OcrJobData, OcrJobStatus
from app.dtos.prescriptions import UpdateExtractedFieldRequest
from app.models.async_jobs import AiJobType, DomainType
from app.models.ocr import ExtractedField, FieldType, OcrJob
from app.models.users import User
from app.repositories.medical_document_repository import (
    DocumentLockTimeoutError,
    MedicalDocumentRepository,
)
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.job_intake import DomainReference, JobIntakeService
from app.services.job_status import JobStatusResult, JobStatusService
from app.services.ocr_engine import (
    NotConfiguredOcrEngine,
    OcrDeadline,
    OcrDeadlineExceededError,
    OcrEngine,
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
)

# 실제 예외 메시지를 그대로 저장하면 처방전 파일 정보가 노출될 수 있어 고정된 문구만 저장합니다.
_PROVIDER_UNAVAILABLE_ERROR_MESSAGE = "OCR 제공자 호출에 실패했습니다."
_ENGINE_ERROR_MESSAGE = "OCR 처리 중 오류가 발생했습니다."

# 사용자가 OCR 오인식 값을 제거하고 “값 없음”으로 확인할 수 있는 필드입니다.
_NULLABLE_CONFIRMED_FIELD_TYPES = frozenset(
    {
        FieldType.MEDICATION_STRENGTH,
        FieldType.DOSE_UNIT,
        FieldType.TIMING,
    }
)


def _to_field_data(
    field: ExtractedField,
) -> ExtractedFieldData:
    return ExtractedFieldData(
        field_id=field.id,
        field_type=str(field.field_type),
        medication_index=field.medication_index,
        raw_value=field.raw_value,
        normalized_value=field.normalized_value,
        confirmed_value=field.confirmed_value,
        confidence_score=(float(field.confidence_score) if field.confidence_score is not None else None),
        confirmation_status=str(field.confirmation_status),
        normalization_version=(field.normalization_version),
    )


def _to_job_data(job: OcrJob, fields: list[ExtractedField]) -> OcrJobData:
    return OcrJobData(
        job_id=job.id,
        document_id=job.document_id,
        ocr_status=OcrJobStatus(job.ocr_status),
        error_code=job.error_code,
        error_message=job.error_message,
        engine_name=job.engine_name,
        model_version=job.model_version,
        prompt_version=job.prompt_version,
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
        # 처방 확정 여부를 lock 획득 이후에 다시 확인하기 위해 주입합니다.
        prescription_repository: PrescriptionRepository | None = None,
    ) -> None:
        self._engine: OcrEngine = engine or NotConfiguredOcrEngine()
        self._document_repo = document_repository
        self._ocr_repo = ocr_repository
        self._prescription_repo = prescription_repository

    async def accept_ocr_job(
        self,
        *,
        user: User,
        document_id: UUID,
        request: ExecuteOcrRequest,
        idempotency_key: str,
        trace_id: str,
        job_intake_service: JobIntakeService,
        job_status_service: JobStatusService,
    ) -> JobStatusResult:
        # OCR 접수 Backend 계약: API 요청에서는 Provider를 호출하지 않고 Job/Outbox/placeholder만
        # 같은 transaction에 저장한 뒤 공통 Job 상태 응답을 반환합니다. 실제 OCR 실행은 Worker가 담당합니다.
        async def create_domain_placeholder(ai_job_id: UUID) -> DomainReference:
            document = await self._document_repo.get_owned(document_id=document_id, user=user)
            if document is None:
                raise ApiError(
                    status_code=404,
                    code="MEDICAL_DOCUMENT_NOT_FOUND",
                    message="의료문서를 찾을 수 없습니다.",
                    details=[
                        ErrorDetail(
                            field="document_id",
                            reason="NOT_FOUND",
                            rejected_value=str(document_id),
                        )
                    ],
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

            ocr_job = await self._ocr_repo.create_job(document=document, ai_job_id=ai_job_id)
            return DomainReference(domain_type=DomainType.OCR_JOB, domain_id=ocr_job.id)

        intake_result = await job_intake_service.accept_job(
            user_id=user.id,
            job_type=AiJobType.OCR,
            operation_id="ocr.create_job",
            idempotency_key=idempotency_key,
            fingerprint={
                "job_type": AiJobType.OCR.value,
                "document_id": str(document_id),
                "force_reprocess": request.force_reprocess,
            },
            create_domain_placeholder=create_domain_placeholder,
            trace_id=trace_id,
        )
        return await job_status_service.get_job_status(user=user, job_id=intake_result.job.id)

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

        # 요청 전체 예산은 wall clock 변경에 영향받지 않도록 monotonic으로 계산합니다.
        # 응답 생성과 실패 상태 저장 여유를 제외한 시점이 Provider 경로의 hard stop입니다.
        deadline = OcrDeadline.start(
            total_seconds=config.OCR_REQUEST_DEADLINE_SECONDS,
            response_margin_seconds=config.OCR_RESPONSE_MARGIN_SECONDS,
        )

        try:
            result = await self._engine.recognize(
                object_key=document.object_key,
                file_mime_type=document.file_mime_type,
                deadline=deadline,
            )
        except OcrDeadlineExceededError:
            # 남은 예산이 없어 Provider를 호출하지 않은 경우입니다.
            # OcrProcessingError의 하위 클래스이므로 반드시 그보다 앞에서 잡습니다.
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_PROVIDER_TIMEOUT",
                error_message="OCR 처리 시간이 초과되었습니다.",
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=503,
                code="OCR_PROVIDER_TIMEOUT",
                message="OCR 서비스 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="ocr", reason="DEADLINE_EXCEEDED")],
            ) from None
        except OcrProviderTimeoutError:
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_PROVIDER_TIMEOUT",
                error_message="OCR 서비스 응답 시간이 초과되었습니다.",
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=503,
                code="OCR_PROVIDER_TIMEOUT",
                message="OCR 서비스 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
                details=[
                    ErrorDetail(
                        field="provider",
                        reason="PROVIDER_TIMEOUT",
                    )
                ],
            ) from None
        except OcrProviderConnectionError:
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_PROVIDER_CALL_FAILED",
                error_message="OCR 제공자 연결에 실패했습니다.",
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=503,
                code="OCR_PROVIDER_CALL_FAILED",
                message="OCR 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.",
                details=[
                    ErrorDetail(
                        field="provider",
                        reason="CONNECTION_FAILED",
                    )
                ],
            ) from None
        except OcrProviderUnavailableError:
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_PROVIDER_UNAVAILABLE",
                error_message=_PROVIDER_UNAVAILABLE_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=503,
                code="OCR_PROVIDER_UNAVAILABLE",
                message="OCR 서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="provider", reason="PROVIDER_UNAVAILABLE")],
            ) from None
        except OcrProcessingError:
            # Provider/OCR 예외 원문에는 민감한 OCR 응답이 포함될 수 있으므로
            # API 예외 체인과 로그에 원문을 남기지 않습니다.
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_PROCESSING_FAILED",
                error_message=_ENGINE_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=500,
                code="OCR_PROCESSING_FAILED",
                message="처방전 인식에 실패했습니다. 다시 시도하거나 직접 입력해 주세요.",
                details=[ErrorDetail(field="ocr", reason="OCR_ENGINE_ERROR")],
            ) from None
        except Exception:
            await self._ocr_repo.mark_failed(
                job,
                error_code="OCR_PROCESSING_FAILED",
                error_message=_ENGINE_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            raise ApiError(
                status_code=500,
                code="OCR_PROCESSING_FAILED",
                message="처방전 인식에 실패했습니다. 다시 시도하거나 직접 입력해 주세요.",
                details=[ErrorDetail(field="ocr", reason="OCR_ENGINE_ERROR")],
            ) from None

        await self._ocr_repo.replace_fields(
            ocr_job=job,
            fields=[
                {
                    "medication_index": (field.medication_index),
                    "field_type": field.field_type,
                    "raw_value": field.raw_value,
                    "normalized_value": (field.normalized_value),
                    "normalization_version": (field.normalization_version),
                    "confidence_score": (field.confidence_score),
                }
                for field in result.fields
            ],
        )

        job = await self._ocr_repo.mark_completed(
            job,
            completed_at=datetime.now(UTC),
            engine_name=result.engine_name,
            model_version=result.model_version,
            prompt_version=result.prompt_version,
        )

        saved_fields = await self._ocr_repo.get_fields_for_job(ocr_job_id=job.id)
        return _to_job_data(job, saved_fields)

    async def get_ocr_job_result(self, *, user: User, job_id: UUID) -> OcrJobData:
        job = await self._ocr_repo.get_job_owned(job_id=job_id, user_id=user.id)
        if job is None:
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
                details=[
                    ErrorDetail(
                        field="field_id",
                        reason="NOT_FOUND",
                        rejected_value=str(field_id),
                    )
                ],
            )

        # 소유권 확인까지는 lock 없이 읽습니다.
        # 잠금 없는 SELECT는 lock을 획득하지 않으므로 전역 lock 순서에 영향을 주지 않습니다.
        # 확보한 document_id로 확정 경로와 같은 MEDICAL_DOCUMENT row를 잠가 두 요청을 직렬화합니다.
        try:
            document = await self._document_repo.get_owned_for_update(
                document_id=field.ocr_job.document_id,
                user=user,
            )
        except DocumentLockTimeoutError:
            raise ApiError(
                status_code=409,
                code="CONCURRENT_UPDATE_IN_PROGRESS",
                message="같은 문서에 대한 다른 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="field_id", reason="CONCURRENT_UPDATE_IN_PROGRESS")],
            ) from None

        if document is None:
            # 위에서 소유권을 확인했으므로 여기 도달하면 문서가 동시에 삭제된 경우입니다.
            raise ApiError(
                status_code=404,
                code="MEDICAL_DOCUMENT_NOT_FOUND",
                message="의료문서를 찾을 수 없습니다.",
                details=[
                    ErrorDetail(
                        field="field_id",
                        reason="NOT_FOUND",
                        rejected_value=str(field_id),
                    )
                ],
            )

        # PRESCRIPTION은 사용자 검수를 마친 최종 확정 데이터입니다.
        # 처방 확정 이후 OCR 추출값이 변경되면 화면의 검수값과 확정 처방이 달라질 수 있으므로
        # 추가 PATCH를 거부하고 Frontend가 비편집 확정 화면으로 전환하도록 합니다.
        #
        # field.ocr_job.document.prescription은 lock 획득 전에 eager loading 된 값이라
        # 그 사이에 확정된 처방을 놓칠 수 있습니다. lock 이후 새로 조회해야 정확합니다.
        if self._prescription_repo is None:
            raise RuntimeError("prescription_repository가 주입되지 않았습니다.")

        if await self._prescription_repo.get_by_document(document=document) is not None:
            raise ApiError(
                status_code=409,
                code="PRESCRIPTION_ALREADY_CONFIRMED",
                message="이미 확정된 처방 정보입니다.",
                details=[
                    ErrorDetail(
                        field="document_id",
                        reason="ALREADY_CONFIRMED",
                    )
                ],
            )
        if request.confirmed_value is None and field.field_type not in _NULLABLE_CONFIRMED_FIELD_TYPES:
            raise ApiError(
                status_code=422,
                code="VALIDATION_FAILED",
                message="입력값을 확인해 주세요.",
                details=[
                    ErrorDetail(
                        field="confirmed_value",
                        reason="REQUIRED",
                    )
                ],
            )

        field = await self._ocr_repo.confirm_field(
            field,
            confirmed_value=request.confirmed_value,
            confirmed_at=datetime.now(UTC),
        )
        return _to_field_data(field)
