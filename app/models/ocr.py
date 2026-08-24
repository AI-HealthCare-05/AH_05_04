from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.medical_documents import MedicalDocument
    from app.models.prescriptions import Prescription


class OcrStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FieldType(StrEnum):
    MEDICATION_NAME = "MEDICATION_NAME"
    DOSE_VALUE = "DOSE_VALUE"
    DOSE_UNIT = "DOSE_UNIT"
    FREQUENCY_PER_DAY = "FREQUENCY_PER_DAY"
    TIMING = "TIMING"
    PRESCRIBED_DATE = "PRESCRIBED_DATE"
    DURATION_DAYS = "DURATION_DAYS"


class ConfirmationStatus(StrEnum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"


class OcrJob(Base):
    __tablename__ = "ocr_job"
    __table_args__ = (
        Index("idx_ocr_document_created", "document_id", "created_at", "id"),
        Index("idx_ocr_document_created_seq", "document_id", "created_at", "created_sequence"),
        UniqueConstraint("id", "document_id", name="uq_ocr_job_id_document"),
        CheckConstraint(
            "ocr_status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')",
            name="chk_ocr_status",
        ),
        CheckConstraint(
            "(ocr_status IN ('PENDING', 'PROCESSING') AND completed_at IS NULL) "
            "OR (ocr_status IN ('COMPLETED', 'FAILED') AND completed_at IS NOT NULL)",
            name="chk_ocr_terminal_fields",
        ),
        CheckConstraint("ocr_status <> 'FAILED' OR error_code IS NOT NULL", name="chk_ocr_failed_error"),
        CheckConstraint(
            "ocr_status <> 'COMPLETED' OR (error_code IS NULL AND error_message IS NULL)",
            name="chk_ocr_exclusive_result",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    created_sequence: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        nullable=False,
        server_default=text("(UUID_SHORT())"),
    )
    document_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("medical_document.id"), nullable=False)
    ocr_status: Mapped[OcrStatus] = mapped_column(
        Enum(OcrStatus, native_enum=False, length=20),
        nullable=False,
        default=OcrStatus.PENDING,
    )
    engine_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    document: Mapped["MedicalDocument"] = relationship(back_populates="ocr_jobs")
    extracted_fields: Mapped[list["ExtractedField"]] = relationship(back_populates="ocr_job")
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="source_ocr_job")


class ExtractedField(Base):
    __tablename__ = "extracted_field"
    __table_args__ = (
        UniqueConstraint(
            "ocr_job_id",
            "medication_index",
            "field_type",
            name="uq_extracted_field_identity",
        ),
        CheckConstraint(
            "field_type IN ("
            "'MEDICATION_NAME', 'DOSE_VALUE', 'DOSE_UNIT', 'FREQUENCY_PER_DAY', "
            "'TIMING', 'PRESCRIBED_DATE', 'DURATION_DAYS')",
            name="chk_field_type",
        ),
        CheckConstraint(
            "confirmation_status IN ('UNCONFIRMED', 'CONFIRMED')",
            name="chk_field_confirmation_status",
        ),
        CheckConstraint(
            "(field_type = 'PRESCRIBED_DATE' AND medication_index = 0) "
            "OR (field_type <> 'PRESCRIBED_DATE' AND medication_index > 0)",
            name="chk_field_medication_index",
        ),
        CheckConstraint(
            "(confirmation_status = 'CONFIRMED' AND confirmed_value IS NOT NULL AND confirmed_at IS NOT NULL) "
            "OR (confirmation_status = 'UNCONFIRMED' AND confirmed_value IS NULL AND confirmed_at IS NULL)",
            name="chk_field_confirmation_fields",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="chk_field_confidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    ocr_job_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("ocr_job.id"), nullable=False)
    medication_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    field_type: Mapped[FieldType] = mapped_column(Enum(FieldType, native_enum=False, length=30), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )
    normalization_version: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    confirmed_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    confirmation_status: Mapped[ConfirmationStatus] = mapped_column(
        Enum(ConfirmationStatus, native_enum=False, length=20),
        nullable=False,
        default=ConfirmationStatus.UNCONFIRMED,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    ocr_job: Mapped["OcrJob"] = relationship(back_populates="extracted_fields")
