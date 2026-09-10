from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedules import (
    MedicationOccurrence,
    MedicationOccurrenceStatus,
    MedicationSchedule,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
    MedicationScheduleStatus,
    MedicationScheduleTime,
)
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.repositories.profile_ownership import owned_by_self


class PrescriptionVersionMedicationOwnership(Protocol):
    """Stable version medication의 SELF 소유권 조회 경계."""

    async def is_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> bool: ...

    async def lock_active_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> bool: ...


class SqlAlchemyPrescriptionVersionMedicationOwnership:
    """#169 parent chain을 사용하는 SELF 소유권 adapter."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> bool:
        medication_id = await self.session.scalar(
            select(PrescriptionVersionMedication.id)
            .join(
                PrescriptionVersion,
                PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id,
            )
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(
                PrescriptionVersionMedication.id == prescription_version_medication_id,
                owned_by_self(Prescription.profile_id, user_id),
            )
        )
        return medication_id is not None

    async def lock_active_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> bool:
        """활성 Version 소유권을 확인하면서 Prescription row를 직렬화한다.

        Schedule 생성과 처방 Version 활성화가 경쟁해도 둘 다 ``PRESCRIPTION``을 먼저
        잠그므로, 확인 뒤 insert 사이에 대상 Version이 과거 Version으로 바뀌지 않는다.
        """

        medication_id = await self.session.scalar(
            select(PrescriptionVersionMedication.id)
            .join(
                PrescriptionVersion,
                PrescriptionVersion.id == PrescriptionVersionMedication.prescription_version_id,
            )
            .join(Prescription, Prescription.id == PrescriptionVersion.prescription_id)
            .where(
                PrescriptionVersionMedication.id == prescription_version_medication_id,
                Prescription.active_version_id == PrescriptionVersion.id,
                owned_by_self(Prescription.profile_id, user_id),
            )
            .with_for_update(of=Prescription)
        )
        return medication_id is not None


def as_utc_instant(value: datetime, *, field: str) -> datetime:
    """DB에 넣을 instant를 aware UTC datetime으로 정규화한다."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class MedicationScheduleRepository:
    """B2~B5에서 재사용하는 Schedule·Occurrence 저장 경계.

    기본 adapter는 #169의 Version parent chain으로 SELF 소유권을 확인한다. 테스트 등에서
    같은 Protocol 구현을 주입할 수 있으며 기존 ``medication.id``로 fallback하지 않는다.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        ownership: PrescriptionVersionMedicationOwnership | None = None,
    ) -> None:
        self.session = session
        self.ownership = ownership or SqlAlchemyPrescriptionVersionMedicationOwnership(session)

    async def create_schedule_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
        start_local_date: date,
        end_mode: MedicationScheduleEndMode,
        end_local_date: date | None,
        source: MedicationScheduleSource,
        status: MedicationScheduleStatus = MedicationScheduleStatus.ACTIVE,
        revision: int = 1,
    ) -> MedicationSchedule | None:
        if not await self.ownership.lock_active_owned(
            prescription_version_medication_id=prescription_version_medication_id,
            user_id=user_id,
        ):
            return None
        schedule = MedicationSchedule(
            prescription_version_medication_id=prescription_version_medication_id,
            start_local_date=start_local_date,
            end_mode=end_mode,
            end_local_date=end_local_date,
            source=source,
            status=status,
            revision=revision,
        )
        self.session.add(schedule)
        await self.session.flush()
        return schedule

    async def add_schedule_times(
        self,
        *,
        schedule: MedicationSchedule,
        schedule_revision: int,
        local_times: Sequence[time],
    ) -> list[MedicationScheduleTime]:
        if schedule_revision != schedule.revision:
            raise ValueError("schedule_revision must match schedule revision")

        rows = [
            MedicationScheduleTime(
                medication_schedule_id=schedule.id,
                schedule_revision=schedule_revision,
                local_time=local_time,
            )
            for local_time in local_times
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def create_occurrence(
        self,
        *,
        schedule: MedicationSchedule,
        schedule_time: MedicationScheduleTime,
        scheduled_local_date: date,
        scheduled_at: datetime,
        confirmation_deadline_at: datetime,
        status: MedicationOccurrenceStatus = MedicationOccurrenceStatus.PENDING,
    ) -> MedicationOccurrence:
        scheduled_at_utc = as_utc_instant(scheduled_at, field="scheduled_at")
        deadline_at_utc = as_utc_instant(confirmation_deadline_at, field="confirmation_deadline_at")
        if deadline_at_utc < scheduled_at_utc:
            raise ValueError("confirmation_deadline_at must not be earlier than scheduled_at")
        if schedule_time.medication_schedule_id != schedule.id:
            raise ValueError("schedule_time must belong to schedule")
        if schedule_time.schedule_revision != schedule.revision:
            raise ValueError("schedule_time revision must match schedule revision")

        occurrence = MedicationOccurrence(
            medication_schedule_id=schedule.id,
            medication_schedule_time_id=schedule_time.id,
            schedule_revision=schedule.revision,
            scheduled_local_date=scheduled_local_date,
            scheduled_at=scheduled_at_utc,
            confirmation_deadline_at=deadline_at_utc,
            status=status,
        )
        self.session.add(occurrence)
        await self.session.flush()
        return occurrence

    async def list_generation_targets_for_update(
        self,
        *,
        horizon_start: date,
        horizon_end: date,
    ) -> list[tuple[MedicationSchedule, MedicationScheduleTime]]:
        """활성 Version의 현재 Schedule revision만 생성 대상으로 잠금 조회한다."""

        rows = await self.session.execute(
            select(MedicationSchedule, MedicationScheduleTime)
            .join(
                MedicationScheduleTime,
                MedicationScheduleTime.medication_schedule_id == MedicationSchedule.id,
            )
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
                Prescription.active_version_id == PrescriptionVersion.id,
                MedicationSchedule.status == MedicationScheduleStatus.ACTIVE,
                MedicationSchedule.start_local_date <= horizon_end,
                or_(
                    MedicationSchedule.end_local_date.is_(None),
                    MedicationSchedule.end_local_date >= horizon_start,
                ),
                MedicationScheduleTime.schedule_revision == MedicationSchedule.revision,
            )
            .order_by(
                Prescription.id,
                MedicationSchedule.id,
                MedicationScheduleTime.local_time,
                MedicationScheduleTime.id,
            )
            .with_for_update(of=[Prescription, MedicationSchedule])
        )
        return [(schedule, schedule_time) for schedule, schedule_time in rows.all()]

    async def create_occurrence_if_absent(
        self,
        *,
        schedule: MedicationSchedule,
        schedule_time: MedicationScheduleTime,
        scheduled_local_date: date,
        scheduled_at: datetime,
        confirmation_deadline_at: datetime,
    ) -> UUID | None:
        """DB unique를 직렬화 기준으로 occurrence를 조건부 생성한다."""

        scheduled_at_utc = as_utc_instant(scheduled_at, field="scheduled_at")
        deadline_at_utc = as_utc_instant(confirmation_deadline_at, field="confirmation_deadline_at")
        if deadline_at_utc < scheduled_at_utc:
            raise ValueError("confirmation_deadline_at must not be earlier than scheduled_at")
        if schedule_time.medication_schedule_id != schedule.id:
            raise ValueError("schedule_time must belong to schedule")
        if schedule_time.schedule_revision != schedule.revision:
            raise ValueError("schedule_time revision must match schedule revision")

        statement = (
            pg_insert(MedicationOccurrence)
            .values(
                medication_schedule_id=schedule.id,
                medication_schedule_time_id=schedule_time.id,
                schedule_revision=schedule.revision,
                scheduled_local_date=scheduled_local_date,
                scheduled_at=scheduled_at_utc,
                confirmation_deadline_at=deadline_at_utc,
                status=MedicationOccurrenceStatus.PENDING,
            )
            .on_conflict_do_nothing(constraint="uq_medication_occurrence_time_local_date")
            .returning(MedicationOccurrence.id)
        )
        return await self.session.scalar(statement)

    async def mark_expired_schedules_ended(self, *, local_date: date) -> tuple[UUID, ...]:
        """종료일이 지난 활성 Schedule만 Scheduler 소유 상태인 ENDED로 전환한다."""

        schedules = list(
            (
                await self.session.execute(
                    select(MedicationSchedule)
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
                        Prescription.active_version_id == PrescriptionVersion.id,
                        MedicationSchedule.status == MedicationScheduleStatus.ACTIVE,
                        MedicationSchedule.end_mode == MedicationScheduleEndMode.DATE,
                        MedicationSchedule.end_local_date < local_date,
                    )
                    .order_by(Prescription.id, MedicationSchedule.id)
                    .with_for_update(of=[Prescription, MedicationSchedule])
                )
            )
            .scalars()
            .all()
        )
        for schedule in schedules:
            schedule.status = MedicationScheduleStatus.ENDED
        if schedules:
            await self.session.flush()
        return tuple(schedule.id for schedule in schedules)

    async def cancel_future_for_prescription_version(
        self,
        *,
        prescription_version_id: UUID,
        effective_at: datetime,
    ) -> tuple[UUID, ...]:
        """이전 Version의 effective 시각 이후 PENDING occurrence만 취소한다.

        반환 ID는 B5가 같은 transaction에서 미전달 알림만 취소할 수 있는 연동
        경계다. Schedule·ScheduleTime·과거/응답 완료 이력은 변경하지 않는다.
        호출자는 계약의 전역 순서에 따라 ``PRESCRIPTION``을 먼저 잠가야 한다.
        """

        effective_at_utc = as_utc_instant(effective_at, field="effective_at")
        occurrences = list(
            (
                await self.session.execute(
                    select(MedicationOccurrence)
                    .join(
                        MedicationSchedule,
                        MedicationSchedule.id == MedicationOccurrence.medication_schedule_id,
                    )
                    .join(
                        PrescriptionVersionMedication,
                        PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
                    )
                    .where(
                        PrescriptionVersionMedication.prescription_version_id == prescription_version_id,
                        MedicationOccurrence.status == MedicationOccurrenceStatus.PENDING,
                        MedicationOccurrence.scheduled_at >= effective_at_utc,
                    )
                    .order_by(MedicationOccurrence.id)
                    .with_for_update(of=MedicationOccurrence)
                )
            )
            .scalars()
            .all()
        )
        for occurrence in occurrences:
            occurrence.status = MedicationOccurrenceStatus.CANCELLED
        if occurrences:
            await self.session.flush()
        return tuple(occurrence.id for occurrence in occurrences)

    async def get_schedule_owned(self, *, schedule_id: UUID, user_id: UUID) -> MedicationSchedule | None:
        schedule = await self.session.get(MedicationSchedule, schedule_id)
        if schedule is None:
            return None
        if not await self.ownership.is_owned(
            prescription_version_medication_id=schedule.prescription_version_medication_id,
            user_id=user_id,
        ):
            return None
        return schedule

    async def get_occurrence_owned(self, *, occurrence_id: UUID, user_id: UUID) -> MedicationOccurrence | None:
        occurrence = await self.session.get(MedicationOccurrence, occurrence_id)
        if occurrence is None:
            return None
        schedule = await self.session.get(MedicationSchedule, occurrence.medication_schedule_id)
        if schedule is None:
            return None
        if not await self.ownership.is_owned(
            prescription_version_medication_id=schedule.prescription_version_medication_id,
            user_id=user_id,
        ):
            return None
        return occurrence
