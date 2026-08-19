from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class MedicalDocumentType(StrEnum):
    PRESCRIPTION = "PRESCRIPTION"


class UploadStatus(StrEnum):
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


class PrescriptionDocumentUploadData(BaseModel):
    document_id: UUID
    upload_status: UploadStatus
    uploaded_at: datetime


class PrescriptionDocumentUploadResponse(BaseModel):
    data: PrescriptionDocumentUploadData
    message: str
