import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath
from uuid import UUID

from fastapi import UploadFile, status

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.dtos.medical_documents import MedicalDocumentType
from app.models.medical_documents import MedicalDocument
from app.models.users import User
from app.repositories.medical_document_repository import MedicalDocumentRepository

MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".png", ".pdf"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "application/pdf"}
CONTENT_TYPE_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}
FILE_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/pdf": (b"%PDF-",),
}


@dataclass(frozen=True)
class PrescriptionDocumentUploadResult:
    document_id: UUID
    upload_status: str
    uploaded_at: datetime


@dataclass(frozen=True)
class MedicalDocumentFileResult:
    file_path: str
    filename: str
    media_type: str


class MedicalDocumentService:
    def __init__(self, repository: MedicalDocumentRepository) -> None:
        self._repo = repository

    async def create_prescription_document(
        self,
        *,
        user: User,
        file: UploadFile,
        document_type: MedicalDocumentType,
    ) -> PrescriptionDocumentUploadResult:
        # 1차 구현 원사이클: JPG/PNG/PDF 처방전 한 장 업로드만 지원합니다. OCR 실행은 별도 API에서 처리합니다.
        if document_type != MedicalDocumentType.PRESCRIPTION:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="VALIDATION_FAILED",
                message="MVP에서는 처방전 문서만 업로드할 수 있습니다.",
                details=[ErrorDetail(field="document_type", reason="INVALID_VALUE", rejected_value=str(document_type))],
            )

        content = await file.read()
        extension = self._validate_file(file=file, content=content)

        document = await self._repo.create(
            user=user,
            original_file_name=file.filename or "prescription",
            object_key="",
            file_mime_type=file.content_type or "",
            file_size_bytes=len(content),
        )

        object_key = self._save_to_storage(document_id=document.id, extension=extension, content=content)
        await self._repo.update_object_key(document, object_key)

        return PrescriptionDocumentUploadResult(
            document_id=document.id,
            upload_status=document.upload_status,
            uploaded_at=document.uploaded_at,
        )

    async def get_prescription_document_file(
        self,
        *,
        user: User,
        document_id: UUID,
    ) -> MedicalDocumentFileResult:
        document = await self._repo.get_owned(document_id=document_id, user=user)
        if document is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="MEDICAL_DOCUMENT_NOT_FOUND",
                message="의료문서를 찾을 수 없습니다.",
                details=[ErrorDetail(field="document_id", reason="NOT_FOUND", rejected_value=str(document_id))],
            )
        return MedicalDocumentFileResult(
            file_path=os.path.join(config.STORAGE_DIR, document.object_key),
            filename=document.original_file_name,
            media_type=document.file_mime_type,
        )

    def _save_to_storage(self, *, document_id: UUID, extension: str, content: bytes) -> str:
        os.makedirs(config.STORAGE_DIR, exist_ok=True)
        object_key = f"{document_id}{extension}"
        with open(os.path.join(config.STORAGE_DIR, object_key), "wb") as f:
            f.write(content)
        return object_key

    def _validate_file(self, *, file: UploadFile, content: bytes) -> str:
        filename = file.filename or ""
        extension = PurePath(filename).suffix.lower()
        content_type = file.content_type or ""

        if not content:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="BAD_REQUEST",
                message="업로드할 파일을 선택해 주세요.",
                details=[ErrorDetail(field="file", reason="REQUIRED")],
            )

        if len(content) > MAX_DOCUMENT_SIZE_BYTES:
            raise ApiError(
                status_code=400,
                code="UPLOAD_FILE_TOO_LARGE",
                message="파일 크기는 10MB 이하만 업로드할 수 있습니다.",
                details=[ErrorDetail(field="file", reason="TOO_LARGE", rejected_value=str(len(content)))],
            )

        expected_content_type = CONTENT_TYPE_BY_EXTENSION.get(extension)
        if (
            extension not in ALLOWED_EXTENSIONS
            or expected_content_type is None
            or content_type not in ALLOWED_CONTENT_TYPES
        ):
            raise ApiError(
                status_code=400,
                code="UPLOAD_FILE_INVALID_TYPE",
                message="지원하지 않는 파일 형식입니다. JPG, PNG, PDF 파일만 업로드할 수 있습니다.",
                details=[ErrorDetail(field="file", reason="INVALID_TYPE", rejected_value=content_type)],
            )

        if content_type != expected_content_type:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="UPLOAD_FILE_INVALID_TYPE",
                message="파일 이름과 파일 형식이 일치하지 않습니다.",
                details=[
                    ErrorDetail(
                        field="file",
                        reason="EXTENSION_MIME_MISMATCH",
                        rejected_value=content_type,
                    )
                ],
            )

        signatures = FILE_SIGNATURES[expected_content_type]
        if not any(content.startswith(signature) for signature in signatures):
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="UPLOAD_FILE_INVALID_TYPE",
                message="파일 형식이 올바르지 않습니다.",
                details=[ErrorDetail(field="file", reason="INVALID_SIGNATURE", rejected_value=content_type)],
            )

        return extension

    async def get_owned_document(self, *, document_id: UUID, user: User) -> MedicalDocument:
        document = await self._repo.get_owned(document_id=document_id, user=user)
        if document is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="MEDICAL_DOCUMENT_NOT_FOUND",
                message="의료문서를 찾을 수 없습니다.",
                details=[ErrorDetail(field="document_id", reason="NOT_FOUND", rejected_value=str(document_id))],
            )
        return document
