from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.models.medical_documents import MedicalDocument
from app.models.users import Gender, User
from app.services.prescriptions import PrescriptionService


@pytest.mark.asyncio
async def test_confirm_prescription_rejects_when_ocr_is_not_completed() -> None:
    user = User(
        id=uuid4(),
        email="ocr-not-completed@example.com",
        hashed_password="hashed-password",
        name="OCR테스터",
        gender=Gender.MALE,
        birthday=datetime(1990, 1, 1).date(),
        phone_number="01012345678",
    )
    document = MedicalDocument(
        id=uuid4(),
        user_id=user.id,
        original_file_name="prescription.jpg",
        object_key="prescription.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=10,
    )

    document_repository = AsyncMock()
    document_repository.get_owned.return_value = document
    ocr_repository = AsyncMock()
    ocr_repository.get_latest_completed_job.return_value = None
    prescription_repository = AsyncMock()
    prescription_repository.get_by_document.return_value = None

    service = PrescriptionService(
        document_repository=document_repository,
        ocr_repository=ocr_repository,
        prescription_repository=prescription_repository,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.confirm_prescription(user=user, document_id=document.id)

    error = exc_info.value
    assert error.status_code == 409
    assert error.code == "OCR_JOB_NOT_COMPLETED"
