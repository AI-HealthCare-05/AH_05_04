from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Row, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import MedicationCheckin, MedicationOccurrence, MedicationSchedule
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.track_c import BarrierResponse, SupportActionPlan, SupportActionPlanStatus
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

    async def list_clinic_context(self, *, user_id: UUID, start_date: date, end_date: date) -> list[Row[Any]]:
        """Answered barriers in the period, with the non-cancelled plan that carries the questions.

        barrier_response is append-only, so only the newest revision of each Check-in
        is the user's current answer; older revisions are corrections and must not
        reach a clinician.
        """
        latest = (
            select(
                BarrierResponse.id.label("barrier_id"),
                BarrierResponse.medication_checkin_id.label("checkin_id"),
                BarrierResponse.barrier_code.label("barrier_code"),
                BarrierResponse.subreason_code.label("subreason_code"),
                func.row_number()
                .over(
                    partition_by=BarrierResponse.medication_checkin_id,
                    order_by=(BarrierResponse.checkin_revision.desc(), BarrierResponse.revision.desc()),
                )
                .label("rank"),
            )
            .where(BarrierResponse.barrier_code.is_not(None))
            .subquery()
        )
        rows = await self.session.execute(
            select(
                MedicationOccurrence.id,
                MedicationOccurrence.scheduled_local_date,
                PrescriptionVersionMedication.medication_name,
                latest.c.barrier_code,
                latest.c.subreason_code,
                SupportActionPlan.support_code,
                SupportActionPlan.action_config_snapshot,
            )
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(PrescriptionVersion, PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id)
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .join(MedicationCheckin, MedicationCheckin.occurrence_id == MedicationOccurrence.id)
            .join(latest, and_(latest.c.checkin_id == MedicationCheckin.id, latest.c.rank == 1))
            .outerjoin(
                SupportActionPlan,
                and_(
                    SupportActionPlan.barrier_response_id == latest.c.barrier_id,
                    SupportActionPlan.status != SupportActionPlanStatus.CANCELLED,
                ),
            )
            .where(
                owned_by_self(Prescription.profile_id, user_id),
                MedicationOccurrence.scheduled_local_date.between(start_date, end_date),
            )
            .order_by(MedicationOccurrence.scheduled_at, MedicationOccurrence.id)
        )
        return list(rows)
