from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.prescription_integrity import require_verified_version
from app.repositories.profile_ownership import owned_by_self


class MedicationScheduleQueries:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def medication_owned(
        self, medication_id: UUID, user_id: UUID, *, active_only: bool = False
    ) -> PrescriptionVersionMedication | None:
        statement = (
            select(PrescriptionVersionMedication)
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(PrescriptionVersionMedication.id == medication_id, owned_by_self(Prescription.profile_id, user_id))
        )
        if active_only:
            # Preview is advisory; only the eventual schedule mutation must serialize with version changes.
            statement = statement.where(Prescription.active_version_id == PrescriptionVersion.id)
        medication = await self.session.scalar(statement)
        if active_only and medication is not None:
            await require_verified_version(self.session, medication.prescription_version_id)
        return medication

    async def active_items(
        self, user_id: UUID
    ) -> list[tuple[PrescriptionVersionMedication, MedicationSchedule | None]]:
        # Match /prescriptions/latest, including its deterministic tie-breaker.
        latest_prescription_id = (
            select(Prescription.id)
            .where(owned_by_self(Prescription.profile_id, user_id))
            .order_by(Prescription.created_at.desc(), Prescription.id.desc())
            .limit(1)
            .correlate(None)
            .scalar_subquery()
        )
        rows = await self.session.execute(
            select(PrescriptionVersionMedication, MedicationSchedule)
            .join(Prescription, Prescription.active_version_id == PrescriptionVersionMedication.prescription_version_id)
            .outerjoin(
                MedicationSchedule,
                MedicationSchedule.prescription_version_medication_id == PrescriptionVersionMedication.id,
            )
            .where(Prescription.id == latest_prescription_id, owned_by_self(Prescription.profile_id, user_id))
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
