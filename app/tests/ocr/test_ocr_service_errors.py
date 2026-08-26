from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.dtos.ocr import ExecuteOcrRequest
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.users import User
from app.repositories.medical_document_repository import (
    MedicalDocumentRepository,
)
from app.repositories.ocr_repository import OcrRepository
from app.services.ocr import OcrService
from app.services.ocr_engine import (
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
    ) -> OcrRecognitionResult:
        _ = object_key, file_mime_type
        raise self._error


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
    ocr_repository_mock.get_job_with_document.return_value = job

    service = OcrService(
        document_repository=cast(MedicalDocumentRepository, document_repository_mock),
        ocr_repository=cast(OcrRepository, ocr_repository_mock),
    )

    result = await service.get_ocr_job_result(user=user, job_id=job_id)

    assert result.error_code == "OCR_PROVIDER_TIMEOUT"
    assert result.error_message == safe_error_message
