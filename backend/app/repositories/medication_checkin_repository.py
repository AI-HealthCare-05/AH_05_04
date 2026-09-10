from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import (
    CheckinAudit,
    MedicationCheckin,
    MedicationCheckinStatus,
    MedicationOccurrence,
    MedicationOccurrenceStatus,
    MedicationSchedule,
)
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.medication_schedule_repository import as_utc_instant
from app.repositories.profile_ownership import owned_by_self


class MedicationCheckinRepository:
    """Check-in 현재값·불변 Audit·deadline 처리를 위한 PostgreSQL 저장 경계."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_occurrence_owned(self, *, occurrence_id: UUID, user_id: UUID) -> MedicationOccurrence | None:
        """SELF parent chain을 확인하며 occurrence를 먼저 잠근다."""

        return await self.session.scalar(
            select(MedicationOccurrence)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(
                PrescriptionVersion,
                PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id,
            )
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(
                MedicationOccurrence.id == occurrence_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update(of=MedicationOccurrence)
            .execution_options(populate_existing=True)
        )

    async def get_current_for_update(self, *, occurrence_id: UUID) -> MedicationCheckin | None:
        return await self.session.scalar(
            select(MedicationCheckin)
            .where(MedicationCheckin.occurrence_id == occurrence_id)
            .with_for_update(of=MedicationCheckin)
            .execution_options(populate_existing=True)
        )

    async def create_if_absent(
        self,
        *,
        occurrence_id: UUID,
        status: MedicationCheckinStatus,
        taken_at: datetime | None,
    ) -> MedicationCheckin | None:
        """Occurrence unique를 기준으로 현재 Check-in을 조건부 생성한다."""

        checkin_id = uuid4()
        inserted_id = await self.session.scalar(
            pg_insert(MedicationCheckin)
            .values(
                id=checkin_id,
                occurrence_id=occurrence_id,
                status=status,
                taken_at=taken_at,
                revision=1,
            )
            .on_conflict_do_nothing(constraint="uq_medication_checkin_occurrence_id")
            .returning(MedicationCheckin.id)
        )
        if inserted_id is None:
            return None
        return await self.session.get(MedicationCheckin, inserted_id)

    async def correct(
        self,
        *,
        checkin: MedicationCheckin,
        status: MedicationCheckinStatus,
        taken_at: datetime | None,
        changed_by: UUID,
        changed_at: datetime,
    ) -> CheckinAudit:
        """현재값을 한 revision 전진시키며 이전 상태를 Audit에 append한다."""

        changed_at_utc = as_utc_instant(changed_at, field="changed_at")
        async with self.session.begin_nested():
            from_revision = checkin.revision
            audit = CheckinAudit(
                checkin_id=checkin.id,
                from_status=checkin.status,
                to_status=status,
                from_revision=from_revision,
                to_revision=from_revision + 1,
                changed_by=changed_by,
                changed_at=changed_at_utc,
            )
            self.session.add(audit)
            checkin.status = status
            checkin.taken_at = taken_at
            checkin.revision = from_revision + 1
            await self.session.flush()
            return audit

    async def close_occurrence(self, *, occurrence: MedicationOccurrence) -> None:
        occurrence.status = MedicationOccurrenceStatus.CLOSED
        await self.session.flush()

    async def list_due_occurrences_for_update(
        self,
        *,
        deadline_at: datetime,
        batch_size: int,
    ) -> Sequence[MedicationOccurrence]:
        """결과 없는 due occurrence를 작은 batch로 잠그고 다른 Scheduler와 분할한다."""

        deadline_at_utc = as_utc_instant(deadline_at, field="deadline_at")
        rows = await self.session.execute(
            select(MedicationOccurrence)
            .outerjoin(MedicationCheckin, MedicationCheckin.occurrence_id == MedicationOccurrence.id)
            .where(
                MedicationOccurrence.status == MedicationOccurrenceStatus.PENDING,
                MedicationOccurrence.confirmation_deadline_at <= deadline_at_utc,
                MedicationCheckin.id.is_(None),
            )
            .order_by(MedicationOccurrence.confirmation_deadline_at, MedicationOccurrence.id)
            .limit(batch_size)
            .with_for_update(of=MedicationOccurrence, skip_locked=True)
        )
        return tuple(rows.scalars().all())

    async def list_unconfirmed_owned(self, *, user_id: UUID, limit: int) -> Sequence[MedicationCheckin]:
        """B4 backlog API가 사용할 SELF 소유 UNCONFIRMED 조회 경계."""

        rows = await self.session.execute(
            select(MedicationCheckin)
            .join(MedicationOccurrence, MedicationOccurrence.id == MedicationCheckin.occurrence_id)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(
                PrescriptionVersion,
                PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id,
            )
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(
                MedicationCheckin.status == MedicationCheckinStatus.UNCONFIRMED,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .order_by(MedicationOccurrence.scheduled_at, MedicationCheckin.id)
            .limit(limit)
        )
        return tuple(rows.scalars().all())

    async def list_audits_owned(
        self,
        *,
        occurrence_id: UUID,
        user_id: UUID,
    ) -> Sequence[CheckinAudit] | None:
        """B4 history API가 사용할 소유권 은닉형 Audit 조회 경계."""

        owned_occurrence = await self.session.scalar(
            select(MedicationOccurrence.id)
            .join(MedicationSchedule, MedicationSchedule.id == MedicationOccurrence.medication_schedule_id)
            .join(
                PrescriptionVersionMedication,
                PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
            )
            .join(
                PrescriptionVersion,
                PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id,
            )
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(
                MedicationOccurrence.id == occurrence_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
        )
        if owned_occurrence is None:
            return None
        rows = await self.session.execute(
            select(CheckinAudit)
            .join(MedicationCheckin, MedicationCheckin.id == CheckinAudit.checkin_id)
            .where(MedicationCheckin.occurrence_id == occurrence_id)
            .order_by(CheckinAudit.to_revision)
        )
        return tuple(rows.scalars().all())

    async def list_unconfirmed_page_owned(
        self, *, user_id: UUID, limit: int, cursor: UUID | None
    ) -> (
        list[tuple[MedicationCheckin, MedicationOccurrence, PrescriptionVersionMedication, PrescriptionVersion]] | None
    ):
        """Owned keyset page; a corrected check-in remains a valid cursor anchor."""
        statement = (
            select(MedicationCheckin, MedicationOccurrence, PrescriptionVersionMedication, PrescriptionVersion)
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
        if cursor is not None:
            anchor = (
                await self.session.execute(
                    statement.with_only_columns(MedicationOccurrence.scheduled_at, MedicationCheckin.id).where(
                        MedicationCheckin.id == cursor
                    )
                )
            ).one_or_none()
            if anchor is None:
                return None
            statement = statement.where(
                or_(
                    MedicationOccurrence.scheduled_at > anchor[0],
                    and_(MedicationOccurrence.scheduled_at == anchor[0], MedicationCheckin.id > anchor[1]),
                )
            )
        rows = await self.session.execute(
            statement.where(MedicationCheckin.status == MedicationCheckinStatus.UNCONFIRMED)
            .order_by(MedicationOccurrence.scheduled_at, MedicationCheckin.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        )
        return [(checkin, occurrence, medication, version) for checkin, occurrence, medication, version in rows]
