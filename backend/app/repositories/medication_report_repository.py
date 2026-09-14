from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.profile_ownership import owned_by_self


class MedicationReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_owned(
        self, *, user_id: UUID, start_date: date, end_date: date
    ) -> list[tuple[MedicationOccurrence, PrescriptionVersionMedication, MedicationCheckin | None]]:
        # One statement gives records and aggregates the same database snapshot,
        # including historical versions without depending on the active schedule.
        rows = await self.session.execute(
            select(MedicationOccurrence, PrescriptionVersionMedication, MedicationCheckin)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .outerjoin(MedicationCheckin, MedicationCheckin.occurrence_id == MedicationOccurrence.id)
            .where(
                owned_by_self(Prescription.profile_id, user_id),
                MedicationOccurrence.scheduled_local_date.between(start_date, end_date),
            )
            .order_by(MedicationOccurrence.scheduled_at, MedicationOccurrence.id)
        )
        return [(occurrence, medication, checkin) for occurrence, medication, checkin in rows]
