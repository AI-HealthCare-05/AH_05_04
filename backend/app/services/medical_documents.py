import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from uuid import UUID

from fastapi import UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.core import config
from app.core.errors import ApiError, ErrorDetail
from app.dtos.medical_documents import MedicalDocumentType
from app.models.medical_documents import MedicalDocument
from app.models.users import User
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.services.document_image_normalizer import normalize_document_image

MAX_DOCUMENT_SIZE_BYTES = 30 * 1024 * 1024
UPLOAD_READ_CHUNK_SIZE_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "application/pdf"}
CONTENT_TYPE_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}
EXTENSION_BY_CONTENT_TYPE = {content_type: extension for extension, content_type in CONTENT_TYPE_BY_EXTENSION.items()}
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

    async def _read_upload_content(self, *, file: UploadFile) -> bytes:
        content = bytearray()
        read_limit = MAX_DOCUMENT_SIZE_BYTES + 1

        while len(content) < read_limit:
            read_size = min(
                UPLOAD_READ_CHUNK_SIZE_BYTES,
                read_limit - len(content),
            )
            chunk = await file.read(read_size)
            if not chunk:
                break
            content.extend(chunk)

        return bytes(content)

    async def create_prescription_document(
        self,
        *,
        user: User,
        file: UploadFile,
        document_type: MedicalDocumentType,
    ) -> PrescriptionDocumentUploadResult:
        # 1차 구현 원사이클: JPG/JPEG/PNG/PDF 처방전 한 장 업로드만 지원합니다. OCR 실행은 별도 API에서 처리합니다.
        if document_type != MedicalDocumentType.PRESCRIPTION:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="VALIDATION_FAILED",
                message="MVP에서는 처방전 문서만 업로드할 수 있습니다.",
                details=[ErrorDetail(field="document_type", reason="INVALID_VALUE", rejected_value=str(document_type))],
            )

        content = await self._read_upload_content(file=file)
        extension = self._validate_file(file=file, content=content)

        normalized = (
            await run_in_threadpool(normalize_document_image, content, file.content_type)
            if file.content_type in {"image/jpeg", "image/png"}
            else None
        )
        document = await self._repo.create(
            user=user,
            original_file_name=file.filename or "prescription",
            object_key="",
            file_mime_type=file.content_type or "",
            file_size_bytes=len(content),
        )

        paths = [Path(config.STORAGE_DIR) / f"{document.id}{extension}"]
        if normalized is not None:
            paths.append(Path(config.STORAGE_DIR) / f"{document.id}.normalized.png")
        self._repo.track_uploaded_files(paths)
        try:
            object_key = self._save_to_storage(document_id=document.id, extension=extension, content=content)
            if normalized is not None:
                document.normalized_object_key = self._save_to_storage(
                    document_id=document.id,
                    extension=".normalized.png",
                    content=normalized.content,
                )
                document.normalized_width = normalized.width
                document.normalized_height = normalized.height
            await self._repo.update_object_key(document, object_key)
        except BaseException:
            for path in paths:
                path.unlink(missing_ok=True)
            raise

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
            filename=self._safe_download_filename(document=document),
            media_type=document.file_mime_type,
        )

    async def get_normalized_document_file(self, *, user: User, document_id: UUID) -> MedicalDocumentFileResult:
        document = await self.get_owned_document(document_id=document_id, user=user)
        key = document.normalized_object_key
        if not key or not document.normalized_width or not document.normalized_height:
            raise ApiError(status_code=404, code="MEDICAL_DOCUMENT_NOT_FOUND", message="의료문서를 찾을 수 없습니다.")
        root = Path(config.STORAGE_DIR).resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ApiError(status_code=404, code="MEDICAL_DOCUMENT_NOT_FOUND", message="의료문서를 찾을 수 없습니다.")
        return MedicalDocumentFileResult(str(path), f"medical-document-{document.id}.png", "image/png")

    def _save_to_storage(self, *, document_id: UUID, extension: str, content: bytes) -> str:
        os.makedirs(config.STORAGE_DIR, exist_ok=True)
        object_key = f"{document_id}{extension}"
        with open(os.path.join(config.STORAGE_DIR, object_key), "xb") as f:
            f.write(content)
        return object_key

    def _safe_download_filename(self, *, document: MedicalDocument) -> str:
        extension = PurePath(document.object_key).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            extension = EXTENSION_BY_CONTENT_TYPE.get(document.file_mime_type, "")

        return f"medical-document-{document.id}{extension}"

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
                message="파일 크기는 30MB 이하만 업로드할 수 있습니다.",
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
                message="지원하지 않는 파일 형식입니다. JPG, JPEG, PNG, PDF 파일만 업로드할 수 있습니다.",
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
