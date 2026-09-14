from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.profile_ownership import owned_by_self


class MedicationScheduleQueries:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def medication_owned(self, medication_id: UUID, user_id: UUID) -> PrescriptionVersionMedication | None:
        return await self.session.scalar(
            select(PrescriptionVersionMedication)
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(PrescriptionVersionMedication.id == medication_id, owned_by_self(Prescription.profile_id, user_id))
        )

    async def active_items(
        self, user_id: UUID
    ) -> list[tuple[PrescriptionVersionMedication, MedicationSchedule | None]]:
        rows = await self.session.execute(
            select(PrescriptionVersionMedication, MedicationSchedule)
            .join(Prescription, Prescription.active_version_id == PrescriptionVersionMedication.prescription_version_id)
            .outerjoin(
                MedicationSchedule,
                MedicationSchedule.prescription_version_medication_id == PrescriptionVersionMedication.id,
            )
            .where(owned_by_self(Prescription.profile_id, user_id))
        )
        return [(medication, schedule) for medication, schedule in rows]

    async def occurrence_medication_owned(
        self, occurrence_id: UUID, user_id: UUID
    ) -> PrescriptionVersionMedication | None:
        # Follow the occurrence's original snapshot, including inactive versions.
        return await self.session.scalar(
            select(PrescriptionVersionMedication)
            .join(
                MedicationSchedule,
                MedicationSchedule.prescription_version_medication_id == PrescriptionVersionMedication.id,
            )
            .join(MedicationOccurrence, MedicationOccurrence.medication_schedule_id == MedicationSchedule.id)
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(MedicationOccurrence.id == occurrence_id, owned_by_self(Prescription.profile_id, user_id))
        )

    async def occurrences(
        self, user_id: UUID, day: date
    ) -> list[tuple[MedicationOccurrence, PrescriptionVersionMedication, MedicationCheckin | None]]:
        # Historical versions remain visible on their occurrence's original KST date.
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
            .where(MedicationOccurrence.scheduled_local_date == day, owned_by_self(Prescription.profile_id, user_id))
        )
        return [(occurrence, medication, checkin) for occurrence, medication, checkin in rows]
