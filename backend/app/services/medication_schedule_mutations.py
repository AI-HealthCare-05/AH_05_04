"""PD-417 storage UoW used by #202 after its idempotency replay check.

The caller owns the transaction and persists the encrypted success snapshot in it.
Notification cancellation must use that same AsyncSession; this service never commits.
"""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.models.medication_schedule_snapshots import ScheduleAuditSnapshot
from app.models.medication_schedules import (
    MedicationSchedule,
    MedicationScheduleStatus,
)
from app.repositories.medication_schedule_repository import MedicationScheduleRepository, as_utc_instant
from app.services.medication_occurrences import (
    UndeliveredMedicationNotificationCancellationPort,
    _inclusive_dates,
)


class ScheduleRevisionConflictError(Exception):
    pass


class ScheduleVersionConflictError(Exception):
    pass


class ScheduleOwnershipNotFoundError(Exception):
    pass


class MedicationScheduleMutationService:
    def __init__(
        self,
        repository: MedicationScheduleRepository,
        *,
        notification_cancellation: UndeliveredMedicationNotificationCancellationPort,
    ) -> None:
        self.repository = repository
        self.notification_cancellation = notification_cancellation

    async def _lock(self, medication_id: UUID, user_id: UUID) -> MedicationSchedule | None:
        ownership = self.repository.ownership
        if not await ownership.is_owned(prescription_version_medication_id=medication_id, user_id=user_id):
            raise ScheduleOwnershipNotFoundError
        if not await ownership.lock_active_owned(prescription_version_medication_id=medication_id, user_id=user_id):
            raise ScheduleVersionConflictError
        return await self.repository.lock_schedule_for_mutation(medication_id)

    async def _cancel_future(self, schedule: MedicationSchedule, effective_at: datetime) -> None:
        occurrence_ids = await self.repository.cancel_future_for_schedule(schedule, effective_at)
        if occurrence_ids:
            await self.notification_cancellation.cancel_undelivered_for_occurrences(
                occurrence_ids=occurrence_ids,
                cancelled_at=effective_at,
            )

    async def put(
        self,
        *,
        medication_id: UUID,
        user_id: UUID,
        expected_revision: int,
        settings: ScheduleAuditSnapshot,
        effective_at: datetime,
    ) -> MedicationSchedule:
        if settings.status != MedicationScheduleStatus.ACTIVE:
            raise ValueError("PUT requires active settings")
        effective_at = as_utc_instant(effective_at, field="effective_at")
        async with self.repository.session.begin_nested():
            schedule = await self._lock(medication_id, user_id)
            if expected_revision != (schedule.revision if schedule is not None else 0):
                raise ScheduleRevisionConflictError
            before = await self.repository.snapshot(schedule) if schedule is not None else None
            if schedule is None:
                schedule = await self.repository.create_schedule_owned(
                    prescription_version_medication_id=medication_id,
                    user_id=user_id,
                    start_local_date=settings.start_local_date,
                    end_mode=settings.end_mode,
                    end_local_date=settings.end_local_date,
                    source=settings.source,
                )
                if schedule is None:
                    raise ScheduleVersionConflictError
            else:
                schedule.revision += 1
                schedule.start_local_date = settings.start_local_date
                schedule.end_mode = settings.end_mode
                schedule.end_local_date = settings.end_local_date
                schedule.source = settings.source
                schedule.status = MedicationScheduleStatus.ACTIVE
            times = await self.repository.add_schedule_times(
                schedule=schedule,
                schedule_revision=schedule.revision,
                local_times=[time.fromisoformat(value) for value in settings.local_times],
            )
            await self.repository.append_audit(
                schedule=schedule,
                before=before,
                changed_by=user_id,
                change_source="USER",
                effective_at=effective_at,
            )
            await self._cancel_future(schedule, effective_at)
            seoul = ZoneInfo("Asia/Seoul")
            today = effective_at.astimezone(seoul).date()
            for day in _inclusive_dates(
                max(today, schedule.start_local_date),
                min(today + timedelta(days=13), schedule.end_local_date or today + timedelta(days=13)),
            ):
                for schedule_time in times:
                    scheduled = datetime.combine(day, schedule_time.local_time, tzinfo=seoul)
                    if scheduled < effective_at:
                        continue
                    deadline = max(
                        datetime.combine(day + timedelta(days=1), time.min, tzinfo=seoul),
                        scheduled + timedelta(hours=4),
                    )
                    await self.repository.create_occurrence_if_absent(
                        schedule=schedule,
                        schedule_time=schedule_time,
                        scheduled_local_date=day,
                        scheduled_at=scheduled.astimezone(UTC),
                        confirmation_deadline_at=deadline.astimezone(UTC),
                    )
            return schedule

    async def cancel(
        self,
        *,
        medication_id: UUID,
        user_id: UUID,
        expected_revision: int,
        effective_at: datetime,
    ) -> MedicationSchedule:
        effective_at = as_utc_instant(effective_at, field="effective_at")
        async with self.repository.session.begin_nested():
            schedule = await self._lock(medication_id, user_id)
            if schedule is None or schedule.revision != expected_revision:
                raise ScheduleRevisionConflictError
            if schedule.status != MedicationScheduleStatus.ACTIVE:
                return schedule
            before = await self.repository.snapshot(schedule)
            schedule.status = MedicationScheduleStatus.CANCELLED
            schedule.revision += 1
            await self.repository.append_audit(
                schedule=schedule,
                before=before,
                changed_by=user_id,
                change_source="USER",
                effective_at=effective_at,
            )
            await self._cancel_future(schedule, effective_at)
            return schedule
