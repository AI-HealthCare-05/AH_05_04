from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.guides import Guide
    from app.models.medical_documents import MedicalDocument
    from app.models.ocr import OcrJob


class PrescriptionStatus(StrEnum):
    # 1차 구현 ERD 범위: PRESCRIPTION row는 확정 시점에만 생성되므로 CONFIRMED만 사용합니다.
    CONFIRMED = "CONFIRMED"


class Prescription(Base):
    __tablename__ = "prescription"
    __table_args__ = (
        Index("idx_prescription_source_ocr", "source_ocr_job_id"),
        CheckConstraint("prescription_status = 'CONFIRMED'", name="chk_prescription_status"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    # 문서당 확정 처방은 최대 1개입니다.
    document_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("medical_document.id"),
        nullable=False,
        unique=True,
    )
    # 처방 생성에 사용한 COMPLETED OCR 작업입니다.
    source_ocr_job_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("ocr_job.id"), nullable=False)
    prescribed_date: Mapped[date] = mapped_column(Date, nullable=False)
    prescription_status: Mapped[PrescriptionStatus] = mapped_column(
        Enum(PrescriptionStatus, native_enum=False, length=20),
        nullable=False,
        default=PrescriptionStatus.CONFIRMED,
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    document: Mapped["MedicalDocument"] = relationship(back_populates="prescription")
    source_ocr_job: Mapped["OcrJob"] = relationship(back_populates="prescriptions")
    # 처방 약물은 OCR·가이드·채팅 등 모든 소비 경로에서 처방전 표시 순서를 유지합니다.
    medications: Mapped[list["Medication"]] = relationship(
        back_populates="prescription",
        order_by=lambda: Medication.display_order,
    )
    guides: Mapped[list["Guide"]] = relationship(back_populates="prescription")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="prescription")


class Medication(Base):
    __tablename__ = "medication"
    __table_args__ = (
        UniqueConstraint("prescription_id", "display_order", name="uq_medication_prescription_order"),
        CheckConstraint("dose_value IS NULL OR dose_value > 0", name="chk_medication_dose"),
        CheckConstraint("frequency_per_day IS NULL OR frequency_per_day > 0", name="chk_medication_frequency"),
        CheckConstraint("duration_days IS NULL OR duration_days > 0", name="chk_medication_duration"),
        CheckConstraint("display_order > 0", name="chk_medication_display_order"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    prescription_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("prescription.id"), nullable=False)
    medication_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # 처방전에 표시된 제품 함량입니다.
    # 복합제와 농도 표현을 보존하기 위해 Decimal이 아닌 문자열로 저장합니다.
    # 예: 100mg, 5mg/100mg, 500mg/5mL
    strength_text: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # 아래 필드는 제품 함량이 아니라 실제 1회 복용량입니다.
    dose_value: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 3),
        nullable=True,
    )
    dose_unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    frequency_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timing_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    prescription: Mapped["Prescription"] = relationship(back_populates="medications")
