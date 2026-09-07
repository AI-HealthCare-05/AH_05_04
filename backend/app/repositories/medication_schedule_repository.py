from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Protocol
from uuid import UUID

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


class PrescriptionVersionMedicationOwnership(Protocol):
    """#169가 제공해야 하는 stable medication 소유권 조회 경계."""

    async def is_owned(
        self,
        *,
        prescription_version_medication_id: UUID,
        user_id: UUID,
    ) -> bool: ...


def as_utc_instant(value: datetime, *, field: str) -> datetime:
    """DB에 넣을 instant를 aware UTC datetime으로 정규화한다."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class MedicationScheduleRepository:
    """B2~B5용 저장 골격.

    API에서 사용할 owned 조회는 #169 소유권 adapter를 반드시 주입한다. 이
    Repository는 stable id를 기존 ``medication.id``로 추정하거나 fallback하지 않는다.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        ownership: PrescriptionVersionMedicationOwnership,
    ) -> None:
        self.session = session
        self.ownership = ownership

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
        if not await self.ownership.is_owned(
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
