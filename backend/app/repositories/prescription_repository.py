from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.repositories.profile_ownership import owned_by_self


class PrescriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_document(self, *, document: MedicalDocument) -> Prescription | None:
        result = await self.session.execute(select(Prescription).where(Prescription.document_id == document.id))
        return result.scalar_one_or_none()

    async def get_owned(self, *, prescription_id: UUID, user_id: UUID) -> Prescription | None:
        result = await self.session.execute(
            select(Prescription)
            .options(selectinload(Prescription.document), selectinload(Prescription.medications))
            .where(
                Prescription.id == prescription_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
        )
        return result.scalar_one_or_none()

    async def create_with_medications(
        self,
        *,
        document: MedicalDocument,
        source_ocr_job: OcrJob,
        prescribed_date: date,
        confirmed_at: datetime,
        medications: list[dict],
    ) -> Prescription:
        prescription = Prescription(
            document_id=document.id,
            source_ocr_job_id=source_ocr_job.id,
            profile_id=document.profile_id,
            prescribed_date=prescribed_date,
            confirmed_at=confirmed_at,
        )
        self.session.add(prescription)
        await self.session.flush()

        for medication in medications:
            self.session.add(Medication(prescription_id=prescription.id, **medication))
        await self.session.flush()
        return prescription

    async def get_medications(self, *, prescription_id: UUID) -> list[Medication]:
        result = await self.session.execute(
            select(Medication)
            .where(Medication.prescription_id == prescription_id)
            .order_by(Medication.display_order.asc())
        )
        return list(result.scalars().all())
