from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.rag_candidate import MedicationIdentification, MedicationIdentificationStatus
from app.repositories.prescription_integrity import require_verified_version
from app.repositories.profile_ownership import owned_by_self


class RagPreflightRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_active_prescription_owned(
        self,
        *,
        prescription_id: UUID,
        user_id: UUID,
    ) -> Prescription | None:
        prescription = await self.session.scalar(
            select(Prescription)
            .where(
                Prescription.id == prescription_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update(of=Prescription)
        )
        if prescription is not None:
            await require_verified_version(self.session, prescription.active_version_id)
        return prescription

    async def lock_prescription_version_for_update(
        self,
        *,
        prescription_version_id: UUID,
    ) -> PrescriptionVersion | None:
        return await self.session.scalar(
            select(PrescriptionVersion)
            .where(PrescriptionVersion.id == prescription_version_id)
            .with_for_update(of=PrescriptionVersion)
        )

    async def list_active_version_medications_for_update(
        self,
        *,
        prescription_version_id: UUID,
    ) -> list[PrescriptionVersionMedication]:
        result = await self.session.execute(
            select(PrescriptionVersionMedication)
            .where(PrescriptionVersionMedication.prescription_version_id == prescription_version_id)
            .order_by(PrescriptionVersionMedication.display_order, PrescriptionVersionMedication.id)
            .with_for_update(of=PrescriptionVersionMedication)
        )
        return list(result.scalars().all())

    async def list_matched_identifications_for_update(
        self,
        *,
        prescription_version_medication_ids: Sequence[UUID],
    ) -> list[MedicationIdentification]:
        if not prescription_version_medication_ids:
            return []

        result = await self.session.execute(
            select(MedicationIdentification)
            .where(
                MedicationIdentification.prescription_version_medication_id.in_(prescription_version_medication_ids),
                MedicationIdentification.status == MedicationIdentificationStatus.MATCHED,
            )
            .with_for_update(of=MedicationIdentification)
        )
        return list(result.scalars().all())
