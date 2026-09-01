from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.ocr import OcrJob
    from app.models.prescriptions import Prescription
    from app.models.profiles import Profile
    from app.models.users import User


class DocumentType(StrEnum):
    PRESCRIPTION = "PRESCRIPTION"


class UploadStatus(StrEnum):
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


class MedicalDocument(Base):
    __tablename__ = "medical_document"
    __table_args__ = (
        Index("idx_document_uploader_uploaded", "uploaded_by", "uploaded_at", "id"),
        Index("idx_document_profile_uploaded", "profile_id", "uploaded_at", "id"),
        CheckConstraint("document_type = 'PRESCRIPTION'", name="chk_document_type_prescription"),
        CheckConstraint("upload_status IN ('UPLOADED', 'FAILED')", name="chk_document_upload_status"),
        CheckConstraint(
            "upload_status <> 'FAILED' OR error_code IS NOT NULL",
            name="chk_document_failed_error",
        ),
        CheckConstraint("file_size_bytes > 0", name="chk_document_file_size"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    uploaded_by: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("profile.id"), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, native_enum=False, length=30),
        nullable=False,
        default=DocumentType.PRESCRIPTION,
    )
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    file_mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, native_enum=False, length=20),
        nullable=False,
        default=UploadStatus.UPLOADED,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    uploader: Mapped["User"] = relationship()
    profile: Mapped["Profile"] = relationship()
    ocr_jobs: Mapped[list["OcrJob"]] = relationship(back_populates="document")
    prescription: Mapped["Prescription | None"] = relationship(back_populates="document")
