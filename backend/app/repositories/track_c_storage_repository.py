"""Track C storage, SELF ownership, and ordered mutation locks."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.track_c import (
    ActionPlanFollowup,
    BarrierCode,
    BarrierResponse,
    BarrierResponseStatus,
    SafetyAssessment,
    SafetyDisposition,
    SafetyResponseLevel,
    SupportActionPlan,
    SupportActionPlanStatus,
)
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

    def _owned_checkin_query(self, *, checkin_id: UUID, user_id: UUID) -> Select[tuple[MedicationCheckin]]:
        return (
            select(MedicationCheckin)
            .join(MedicationOccurrence, MedicationOccurrence.id == MedicationCheckin.occurrence_id)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(MedicationCheckin.id == checkin_id, owned_by_self(Prescription.profile_id, user_id))
        )

    async def get_checkin_owned(self, *, checkin_id: UUID, user_id: UUID) -> MedicationCheckin | None:
        return await self.session.scalar(self._owned_checkin_query(checkin_id=checkin_id, user_id=user_id))

    async def lock_checkin_owned(self, *, checkin_id: UUID, user_id: UUID) -> MedicationCheckin | None:
        """Acquire the first lock in the Track C lock order while enforcing SELF ownership."""
        return await self.session.scalar(
            self._owned_checkin_query(checkin_id=checkin_id, user_id=user_id)
            .with_for_update(of=MedicationCheckin)
            .execution_options(populate_existing=True)
        )

    async def get_latest_safety_for_update(self, *, checkin_id: UUID, checkin_revision: int) -> SafetyAssessment | None:
        return await self.session.scalar(
            select(SafetyAssessment)
            .where(
                SafetyAssessment.medication_checkin_id == checkin_id,
                SafetyAssessment.checkin_revision == checkin_revision,
            )
            .order_by(SafetyAssessment.revision.desc())
            .limit(1)
            .with_for_update(of=SafetyAssessment)
            .execution_options(populate_existing=True)
        )

    async def create_safety(
        self,
        *,
        checkin_id: UUID,
        checkin_revision: int,
        revision: int,
        symptom_codes: list[str],
        response_level: SafetyResponseLevel,
        safety_disposition: SafetyDisposition,
        message_code: str,
        copy_version: str,
        source_version: str,
    ) -> SafetyAssessment:
        assessment = SafetyAssessment(
            medication_checkin_id=checkin_id,
            checkin_revision=checkin_revision,
            revision=revision,
            symptom_codes=symptom_codes,
            response_level=response_level,
            safety_disposition=safety_disposition,
            message_code=message_code,
            copy_version=copy_version,
            source_version=source_version,
        )
        self.session.add(assessment)
        await self.session.flush()
        return assessment

    async def cancel_active_plans_for_checkin_revision(
        self,
        *,
        checkin_id: UUID,
        checkin_revision: int,
        cancelled_at: datetime,
    ) -> int:
        """Lock the historical C graph in global order and cancel active plans only.

        The Track B caller already owns the ``MEDICATION_CHECKIN`` row lock. Reading
        and locking every matching Safety and Barrier row before any ActionPlan row
        keeps C4 on the shared lock order and preserves the complete history.
        """

        safety_rows = await self.session.scalars(
            select(SafetyAssessment)
            .where(
                SafetyAssessment.medication_checkin_id == checkin_id,
                SafetyAssessment.checkin_revision == checkin_revision,
            )
            .order_by(SafetyAssessment.revision, SafetyAssessment.id)
            .with_for_update(of=SafetyAssessment)
        )
        safety_rows.all()
        barriers = tuple(
            (
                await self.session.scalars(
                    select(BarrierResponse)
                    .where(
                        BarrierResponse.medication_checkin_id == checkin_id,
                        BarrierResponse.checkin_revision == checkin_revision,
                    )
                    .order_by(BarrierResponse.revision, BarrierResponse.id)
                    .with_for_update(of=BarrierResponse)
                )
            ).all()
        )
        if not barriers:
            return 0
        barrier_ids = [barrier.id for barrier in barriers]
        plans = tuple(
            (
                await self.session.scalars(
                    select(SupportActionPlan)
                    .where(
                        SupportActionPlan.barrier_response_id.in_(barrier_ids),
                        SupportActionPlan.status == SupportActionPlanStatus.ACTIVE,
                    )
                    .order_by(SupportActionPlan.id)
                    .with_for_update(of=SupportActionPlan)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        for plan in plans:
            plan.status = SupportActionPlanStatus.CANCELLED
            plan.cancelled_at = cancelled_at
        if plans:
            await self.session.flush()
        return len(plans)

    async def get_latest_barrier_for_update(self, *, checkin_id: UUID, checkin_revision: int) -> BarrierResponse | None:
        return await self.session.scalar(
            select(BarrierResponse)
            .where(
                BarrierResponse.medication_checkin_id == checkin_id,
                BarrierResponse.checkin_revision == checkin_revision,
            )
            .order_by(BarrierResponse.revision.desc())
            .limit(1)
            .with_for_update(of=BarrierResponse)
            .execution_options(populate_existing=True)
        )

    async def create_barrier(
        self,
        *,
        checkin_id: UUID,
        checkin_revision: int,
        safety_assessment_id: UUID,
        revision: int,
        response_status: BarrierResponseStatus,
        barrier_code: BarrierCode | None,
    ) -> BarrierResponse:
        response = BarrierResponse(
            medication_checkin_id=checkin_id,
            checkin_revision=checkin_revision,
            safety_assessment_id=safety_assessment_id,
            revision=revision,
            response_status=response_status,
            barrier_code=barrier_code,
        )
        self.session.add(response)
        await self.session.flush()
        return response

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

    async def get_support_flow_owned(
        self, *, barrier_id: UUID, user_id: UUID
    ) -> tuple[BarrierResponse, UUID, MedicationCheckin, SafetyAssessment | None, UUID | None] | None:
        """One MVCC statement snapshot, without row locks or writes, for Offer GET."""
        candidate = aliased(BarrierResponse)
        latest_barrier_id = (
            select(candidate.id)
            .where(
                candidate.medication_checkin_id == MedicationCheckin.id,
                candidate.checkin_revision == MedicationCheckin.revision,
            )
            .order_by(candidate.revision.desc())
            .limit(1)
            .correlate(MedicationCheckin)
            .scalar_subquery()
        )
        latest_safety_id = (
            select(SafetyAssessment.id)
            .where(
                SafetyAssessment.medication_checkin_id == MedicationCheckin.id,
                SafetyAssessment.checkin_revision == MedicationCheckin.revision,
            )
            .order_by(SafetyAssessment.revision.desc())
            .limit(1)
            .correlate(MedicationCheckin)
            .scalar_subquery()
        )
        result = await self.session.execute(
            select(
                BarrierResponse,
                MedicationSchedule.prescription_version_medication_id,
                MedicationCheckin,
                SafetyAssessment,
                latest_barrier_id,
            )
            .select_from(BarrierResponse)
            .join(MedicationCheckin, MedicationCheckin.id == BarrierResponse.medication_checkin_id)
            .join(MedicationOccurrence, MedicationOccurrence.id == MedicationCheckin.occurrence_id)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .outerjoin(SafetyAssessment, SafetyAssessment.id == latest_safety_id)
            .where(BarrierResponse.id == barrier_id, MedicationCheckin.id.in_(self._owned_checkins(user_id)))
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return (row[0], row[1], row[2], row[3], row[4]) if row is not None else None

    async def get_active_plan_for_update(self, *, barrier_id: UUID) -> SupportActionPlan | None:
        """Caller must hold the owned Check-in → Safety → Barrier locks first."""
        return await self.session.scalar(
            select(SupportActionPlan)
            .where(
                SupportActionPlan.barrier_response_id == barrier_id,
                SupportActionPlan.status == SupportActionPlanStatus.ACTIVE,
            )
            .with_for_update(of=SupportActionPlan)
            .execution_options(populate_existing=True)
        )

    async def get_action_plan_for_update(self, *, plan_id: UUID) -> SupportActionPlan | None:
        """Caller holds the owned Check-in → Safety → Barrier locks first."""
        return await self.session.scalar(
            select(SupportActionPlan)
            .where(SupportActionPlan.id == plan_id)
            .with_for_update(of=SupportActionPlan)
            .execution_options(populate_existing=True)
        )

    async def get_action_plan_owned(self, *, plan_id: UUID, user_id: UUID) -> SupportActionPlan | None:
        return await self.session.scalar(
            select(SupportActionPlan)
            .join(BarrierResponse, BarrierResponse.id == SupportActionPlan.barrier_response_id)
            .where(
                SupportActionPlan.id == plan_id,
                BarrierResponse.medication_checkin_id.in_(self._owned_checkins(user_id)),
            )
            .execution_options(populate_existing=True)
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
