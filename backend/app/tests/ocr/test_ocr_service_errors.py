import logging
import traceback
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.core.errors import ApiError
from app.dtos.ocr import ExecuteOcrRequest
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.users import User
from app.repositories.medical_document_repository import (
    DocumentLockTimeoutError,
    MedicalDocumentRepository,
)
from app.repositories.ocr_repository import OcrRepository
from app.services.job_intake import JobIntakeResult, JobIntakeService
from app.services.job_status import JobStatusService
from app.services.ocr import OcrService
from app.services.ocr_engine import (
    OcrDeadline,
    OcrDeadlineExceededError,
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
    OcrRecognitionResult,
)


class RaisingOcrEngine:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: OcrDeadline,
    ) -> OcrRecognitionResult:
        _ = object_key, file_mime_type, deadline
        raise self._error


class SyntheticOcrValidationPayload(BaseModel):
    quantity: int


@pytest.mark.parametrize(
    (
        "provider_error",
        "expected_status",
        "expected_code",
        "expected_reason",
    ),
    [
        (
            OcrProviderTimeoutError("민감한 provider 메시지"),
            503,
            "OCR_PROVIDER_TIMEOUT",
            "PROVIDER_TIMEOUT",
        ),
        (
            OcrProviderConnectionError("민감한 provider 메시지"),
            503,
            "OCR_PROVIDER_CALL_FAILED",
            "CONNECTION_FAILED",
        ),
        (
            OcrProviderUnavailableError("민감한 provider 메시지"),
            503,
            "OCR_PROVIDER_UNAVAILABLE",
            "PROVIDER_UNAVAILABLE",
        ),
        (
            OcrDeadlineExceededError("OCR 요청 예산이 남아 있지 않습니다."),
            503,
            "OCR_PROVIDER_TIMEOUT",
            "DEADLINE_EXCEEDED",
        ),
        (
            OcrProcessingError("민감한 OCR 응답"),
            500,
            "OCR_PROCESSING_FAILED",
            "OCR_ENGINE_ERROR",
        ),
    ],
)
async def test_execute_ocr_converts_engine_error_and_marks_job_failed(
    provider_error: Exception,
    expected_status: int,
    expected_code: str,
    expected_reason: str,
) -> None:
    document_id = uuid4()

    user = cast(
        User,
        SimpleNamespace(id=uuid4()),
    )
    document = cast(
        MedicalDocument,
        SimpleNamespace(
            id=document_id,
            object_key="prescription.png",
            file_mime_type="image/png",
        ),
    )
    job = cast(
        OcrJob,
        SimpleNamespace(id=uuid4()),
    )

    document_repository_mock = AsyncMock(
        spec=MedicalDocumentRepository,
    )
    document_repository_mock.get_owned.return_value = document

    ocr_repository_mock = AsyncMock(
        spec=OcrRepository,
    )
    ocr_repository_mock.get_active_job.return_value = None
    ocr_repository_mock.create_job.return_value = job
    ocr_repository_mock.mark_processing.return_value = job
    ocr_repository_mock.mark_failed.return_value = job

    service = OcrService(
        document_repository=cast(
            MedicalDocumentRepository,
            document_repository_mock,
        ),
        ocr_repository=cast(
            OcrRepository,
            ocr_repository_mock,
        ),
        engine=RaisingOcrEngine(provider_error),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.execute_ocr(
            user=user,
            document_id=document_id,
            request=ExecuteOcrRequest(
                force_reprocess=False,
            ),
        )

    error = exc_info.value

    assert error.status_code == expected_status
    assert error.code == expected_code
    assert error.details[0].reason == expected_reason

    # 외부 서비스가 반환한 원본 오류 메시지는 API에 노출하지 않습니다.
    assert "민감한" not in error.message

    ocr_repository_mock.mark_failed.assert_awaited_once()

    mark_failed_call = ocr_repository_mock.mark_failed.await_args
    assert mark_failed_call.kwargs["error_code"] == expected_code
    assert "민감한" not in mark_failed_call.kwargs["error_message"]


async def test_accept_ocr_job_locks_document_before_active_job_check() -> None:
    user = cast(User, SimpleNamespace(id=uuid4()))
    document = cast(MedicalDocument, SimpleNamespace(id=uuid4()))
    ocr_job = cast(OcrJob, SimpleNamespace(id=uuid4()))
    ai_job = SimpleNamespace(id=uuid4())

    document_repository_mock = AsyncMock(spec=MedicalDocumentRepository)
    document_repository_mock.get_owned_for_update.return_value = document

    ocr_repository_mock = AsyncMock(spec=OcrRepository)
    ocr_repository_mock.get_active_job.return_value = None
    ocr_repository_mock.create_job.return_value = ocr_job

    job_intake_service_mock = AsyncMock(spec=JobIntakeService)

    async def accept_job(**kwargs: object) -> JobIntakeResult:
        placeholder = kwargs["create_domain_placeholder"]
        assert callable(placeholder)
        await placeholder(ai_job.id)
        return JobIntakeResult(job=ai_job, is_duplicate=False)  # type: ignore[arg-type]

    job_intake_service_mock.accept_job.side_effect = accept_job

    job_status_service_mock = AsyncMock(spec=JobStatusService)
    expected_result = SimpleNamespace(data=SimpleNamespace(job_id=ai_job.id))
    job_status_service_mock.get_job_status.return_value = expected_result

    service = OcrService(
        document_repository=cast(MedicalDocumentRepository, document_repository_mock),
        ocr_repository=cast(OcrRepository, ocr_repository_mock),
    )

    result = await service.accept_ocr_job(
        user=user,
        document_id=document.id,
        request=ExecuteOcrRequest(force_reprocess=False),
        idempotency_key="ocr-intake-lock-anchor-0001",
        trace_id="a" * 32,
        job_intake_service=cast(JobIntakeService, job_intake_service_mock),
        job_status_service=cast(JobStatusService, job_status_service_mock),
    )

    assert result is expected_result
    document_repository_mock.get_owned.assert_not_called()
    document_repository_mock.get_owned_for_update.assert_awaited_once_with(document_id=document.id, user=user)
    ocr_repository_mock.get_active_job.assert_awaited_once()
    ocr_repository_mock.create_job.assert_awaited_once_with(document=document, ai_job_id=ai_job.id)


async def test_accept_ocr_job_returns_409_when_document_lock_times_out() -> None:
    user = cast(User, SimpleNamespace(id=uuid4()))
    document_id = uuid4()

    document_repository_mock = AsyncMock(spec=MedicalDocumentRepository)
    document_repository_mock.get_owned_for_update.side_effect = DocumentLockTimeoutError

    ocr_repository_mock = AsyncMock(spec=OcrRepository)
    job_intake_service_mock = AsyncMock(spec=JobIntakeService)

    async def accept_job(**kwargs: object) -> JobIntakeResult:
        placeholder = kwargs["create_domain_placeholder"]
        assert callable(placeholder)
        await placeholder(uuid4())
        raise AssertionError("lock timeout should abort placeholder creation")

    job_intake_service_mock.accept_job.side_effect = accept_job

    service = OcrService(
        document_repository=cast(MedicalDocumentRepository, document_repository_mock),
        ocr_repository=cast(OcrRepository, ocr_repository_mock),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.accept_ocr_job(
            user=user,
            document_id=document_id,
            request=ExecuteOcrRequest(force_reprocess=False),
            idempotency_key="ocr-intake-lock-timeout-0001",
            trace_id="a" * 32,
            job_intake_service=cast(JobIntakeService, job_intake_service_mock),
            job_status_service=cast(JobStatusService, AsyncMock(spec=JobStatusService)),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CONCURRENT_UPDATE_IN_PROGRESS"
    ocr_repository_mock.get_active_job.assert_not_called()
    ocr_repository_mock.create_job.assert_not_called()


async def test_execute_ocr_validation_failure_does_not_expose_recognized_content(
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENTINEL-OCR-VALIDATION-CONTENT"
    with pytest.raises(ValidationError) as validation_error:
        SyntheticOcrValidationPayload.model_validate({"quantity": sentinel})

    document_id = uuid4()
    user = cast(User, SimpleNamespace(id=uuid4()))
    document = cast(
        MedicalDocument,
        SimpleNamespace(id=document_id, object_key="prescription.png", file_mime_type="image/png"),
    )
    job = cast(OcrJob, SimpleNamespace(id=uuid4()))
    document_repository_mock = AsyncMock(spec=MedicalDocumentRepository)
    document_repository_mock.get_owned.return_value = document
    ocr_repository_mock = AsyncMock(spec=OcrRepository)
    ocr_repository_mock.get_active_job.return_value = None
    ocr_repository_mock.create_job.return_value = job
    ocr_repository_mock.mark_processing.return_value = job
    ocr_repository_mock.mark_failed.return_value = job
    service = OcrService(
        document_repository=cast(MedicalDocumentRepository, document_repository_mock),
        ocr_repository=cast(OcrRepository, ocr_repository_mock),
        engine=RaisingOcrEngine(validation_error.value),
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ApiError) as api_error:
        await service.execute_ocr(
            user=user,
            document_id=document_id,
            request=ExecuteOcrRequest(force_reprocess=False),
        )

    captured = capfd.readouterr()
    mark_failed_call = ocr_repository_mock.mark_failed.await_args
    exposed_text = "\n".join(
        (
            captured.out,
            captured.err,
            caplog.text,
            "".join(traceback.format_exception(api_error.value)),
            api_error.value.message,
            str(mark_failed_call.kwargs["error_message"]),
        )
    )
    assert sentinel not in exposed_text


async def test_get_ocr_job_result_exposes_safe_error_message() -> None:
    user_id = uuid4()
    job_id = uuid4()
    document_id = uuid4()
    safe_error_message = "OCR 서비스 응답 시간이 초과되었습니다."

    user = cast(User, SimpleNamespace(id=user_id))
    job = cast(
        OcrJob,
        SimpleNamespace(
            id=job_id,
            document_id=document_id,
            document=SimpleNamespace(user_id=user_id),
            ocr_status=OcrStatus.FAILED,
            error_code="OCR_PROVIDER_TIMEOUT",
            error_message=safe_error_message,
            # 실패한 기존 작업에는 실행 메타데이터가 없을 수 있습니다.
            engine_name=None,
            model_version=None,
            prompt_version=None,
            created_at=datetime(2026, 8, 24, 10, 0, 0, tzinfo=UTC),
            completed_at=datetime(2026, 8, 24, 10, 0, 5, tzinfo=UTC),
            extracted_fields=[],
        ),
    )

    document_repository_mock = AsyncMock(spec=MedicalDocumentRepository)
    ocr_repository_mock = AsyncMock(spec=OcrRepository)
    ocr_repository_mock.get_job_owned.return_value = job

    service = OcrService(
        document_repository=cast(MedicalDocumentRepository, document_repository_mock),
        ocr_repository=cast(OcrRepository, ocr_repository_mock),
    )

    result = await service.get_ocr_job_result(user=user, job_id=job_id)

    assert result.error_code == "OCR_PROVIDER_TIMEOUT"
    assert result.error_message == safe_error_message
