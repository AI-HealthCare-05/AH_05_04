from io import BytesIO
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.core.errors import ApiError
from app.models.medical_documents import MedicalDocument
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
        (
            _file(filename="prescription.jpg", content_type="application/pdf"),
            b"%PDF-1.7 test",
            "UPLOAD_FILE_INVALID_TYPE",
            400,
        ),
        (
            _file(filename="prescription.pdf", content_type="image/jpeg"),
            b"\xff\xd8\xff fake-jpeg",
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
    assert error.message == "파일 크기는 30MB 이하만 업로드할 수 있습니다."


def test_validate_file_accepts_pdf(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription.pdf", content_type="application/pdf")

    extension = service._validate_file(file=file, content=b"%PDF-1.7 test")

    assert extension == ".pdf"


def test_validate_file_accepts_jpg(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription.jpg", content_type="image/jpeg")

    extension = service._validate_file(file=file, content=b"\xff\xd8\xff fake-jpeg")

    assert extension == ".jpg"


def test_validate_file_accepts_jpeg(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription.jpeg", content_type="image/jpeg")

    extension = service._validate_file(file=file, content=b"\xff\xd8\xff fake-jpeg")

    assert extension == ".jpeg"


def test_validate_file_accepts_png(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription.png", content_type="image/png")

    extension = service._validate_file(file=file, content=b"\x89PNG\r\n\x1a\n fake-png")

    assert extension == ".png"


def test_validate_file_accepts_uppercase_extension(service: MedicalDocumentService) -> None:
    file = _file(filename="PRESCRIPTION.JPEG", content_type="image/jpeg")

    extension = service._validate_file(file=file, content=b"\xff\xd8\xff fake-jpeg")

    assert extension == ".jpeg"


def test_validate_file_accepts_file_at_exact_size_limit(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription.jpg", content_type="image/jpeg")
    content = b"\xff\xd8\xff" + b"x" * (MAX_DOCUMENT_SIZE_BYTES - 3)

    extension = service._validate_file(file=file, content=content)

    assert extension == ".jpg"
    assert len(content) == MAX_DOCUMENT_SIZE_BYTES
    assert MAX_DOCUMENT_SIZE_BYTES == 30 * 1024 * 1024


def test_validate_file_rejects_filename_without_extension(service: MedicalDocumentService) -> None:
    file = _file(filename="prescription", content_type="image/jpeg")

    with pytest.raises(ApiError) as exc_info:
        service._validate_file(file=file, content=b"\xff\xd8\xff fake-jpeg")

    error = exc_info.value
    assert error.code == "UPLOAD_FILE_INVALID_TYPE"
    assert error.status_code == 400


def test_safe_download_filename_does_not_reuse_original_filename(service: MedicalDocumentService) -> None:
    document_id = uuid4()
    document = MedicalDocument(
        id=document_id,
        uploaded_by=uuid4(),
        profile_id=uuid4(),
        original_file_name="patient-hypertension-medication.pdf",
        object_key=f"{document_id}.pdf",
        file_mime_type="application/pdf",
        file_size_bytes=100,
    )

    filename = service._safe_download_filename(document=document)

    assert filename == f"medical-document-{document_id}.pdf"
    assert "patient" not in filename
    assert "hypertension" not in filename
    assert "medication" not in filename
