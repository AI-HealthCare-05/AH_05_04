from io import BytesIO
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import UploadFile

from app.core.errors import ApiError
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.services.medical_documents import MAX_DOCUMENT_SIZE_BYTES, MedicalDocumentService


def _file(*, filename: str, content_type: str) -> UploadFile:
    return cast(
        UploadFile,
        SimpleNamespace(filename=filename, content_type=content_type, file=BytesIO()),
    )


@pytest.fixture
def service() -> MedicalDocumentService:
    return MedicalDocumentService(repository=cast(MedicalDocumentRepository, None))


@pytest.mark.parametrize(
    ("file", "content", "expected_code", "expected_status"),
    [
        (_file(filename="prescription.jpg", content_type="image/jpeg"), b"", "BAD_REQUEST", 400),
        (
            _file(filename="prescription.exe", content_type="application/octet-stream"),
            b"not-an-image",
            "UPLOAD_FILE_INVALID_TYPE",
            400,
        ),
        (
            _file(filename="prescription.jpg", content_type="image/jpeg"),
            b"not-a-real-jpeg",
            "UPLOAD_FILE_INVALID_TYPE",
            400,
        ),
    ],
)
def test_validate_file_returns_common_api_error(
    service: MedicalDocumentService,
    file: UploadFile,
    content: bytes,
    expected_code: str,
    expected_status: int,
) -> None:
    with pytest.raises(ApiError) as exc_info:
        service._validate_file(file=file, content=content)

    error = exc_info.value
    assert error.code == expected_code
    assert error.status_code == expected_status


def test_validate_file_rejects_oversized_file(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription.jpg", content_type="image/jpeg")

    with pytest.raises(ApiError) as exc_info:
        service._validate_file(file=file, content=b"x" * (MAX_DOCUMENT_SIZE_BYTES + 1))

    error = exc_info.value
    assert error.code == "UPLOAD_FILE_TOO_LARGE"
    assert error.status_code == 400


def test_validate_file_accepts_pdf(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription.pdf", content_type="application/pdf")

    extension = service._validate_file(file=file, content=b"%PDF-1.7 test")

    assert extension == ".pdf"
