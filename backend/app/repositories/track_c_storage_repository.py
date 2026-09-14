"""C1 storage reads with SELF ownership; C2-C4 own mutation services and locks."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.track_c import ActionPlanFollowup, BarrierResponse, SafetyAssessment, SupportActionPlan
from app.repositories.profile_ownership import owned_by_self


class TrackCStorageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _owned_checkins(self, user_id: UUID) -> Select[tuple[UUID]]:
        return (
            select(MedicationCheckin.id)
            .join(MedicationOccurrence, MedicationOccurrence.id == MedicationCheckin.occurrence_id)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(owned_by_self(Prescription.profile_id, user_id))
        )

    async def get_safety_owned(self, *, assessment_id: UUID, user_id: UUID) -> SafetyAssessment | None:
        return await self.session.scalar(
            select(SafetyAssessment).where(
                SafetyAssessment.id == assessment_id,
                SafetyAssessment.medication_checkin_id.in_(self._owned_checkins(user_id)),
            )
        )

    async def get_barrier_owned(self, *, barrier_id: UUID, user_id: UUID) -> BarrierResponse | None:
        return await self.session.scalar(
            select(BarrierResponse).where(
                BarrierResponse.id == barrier_id,
                BarrierResponse.medication_checkin_id.in_(self._owned_checkins(user_id)),
            )
        )

    async def get_barrier_medication_owned(
        self, *, barrier_id: UUID, user_id: UUID
    ) -> tuple[BarrierResponse, UUID] | None:
        """Resolve the reminder target from the owned parent chain, never a client ID."""
        result = await self.session.execute(
            select(BarrierResponse, PrescriptionVersionMedication.id)
            .join(MedicationCheckin, MedicationCheckin.id == BarrierResponse.medication_checkin_id)
            .join(MedicationOccurrence, MedicationOccurrence.id == MedicationCheckin.occurrence_id)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(BarrierResponse.id == barrier_id, owned_by_self(Prescription.profile_id, user_id))
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def get_action_plan_owned(self, *, plan_id: UUID, user_id: UUID) -> SupportActionPlan | None:
        return await self.session.scalar(
            select(SupportActionPlan)
            .join(BarrierResponse, BarrierResponse.id == SupportActionPlan.barrier_response_id)
            .where(
                SupportActionPlan.id == plan_id,
                BarrierResponse.medication_checkin_id.in_(self._owned_checkins(user_id)),
            )
        )

    async def get_followup_owned(self, *, followup_id: UUID, user_id: UUID) -> ActionPlanFollowup | None:
        return await self.session.scalar(
            select(ActionPlanFollowup)
            .join(SupportActionPlan, SupportActionPlan.id == ActionPlanFollowup.support_action_plan_id)
            .join(BarrierResponse, BarrierResponse.id == SupportActionPlan.barrier_response_id)
            .where(
                ActionPlanFollowup.id == followup_id,
                BarrierResponse.medication_checkin_id.in_(self._owned_checkins(user_id)),
            )
        )
