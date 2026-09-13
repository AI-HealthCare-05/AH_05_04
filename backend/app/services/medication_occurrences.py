from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Protocol
from uuid import UUID

from app.repositories.medication_schedule_repository import MedicationScheduleRepository, as_utc_instant

ROLLING_HORIZON_DAYS = 14
ASIA_SEOUL_NAME = "Asia/Seoul"


@dataclass(frozen=True)
class RollingOccurrenceGenerationResult:
    horizon_start: date
    horizon_end: date
    created_count: int
    duplicate_count: int
    ended_schedule_count: int


class UndeliveredMedicationNotificationCancellationPort(Protocol):
    """B5가 구현할 미전달 알림 취소의 동기 transaction 경계."""

    async def cancel_undelivered_for_occurrences(
        self,
        *,
        occurrence_ids: Sequence[UUID],
        cancelled_at: datetime,
    ) -> None: ...


def _require_asia_seoul(service_timezone: tzinfo) -> None:
    if str(service_timezone) != ASIA_SEOUL_NAME and service_timezone.tzname(None) != ASIA_SEOUL_NAME:
        raise ValueError("Medication occurrence scheduler requires TIMEZONE=Asia/Seoul")


def _aware_instant(value: datetime, *, field: str) -> datetime:
    return as_utc_instant(value, field=field)


def _inclusive_dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


class MedicationOccurrenceScheduler:
    """확인된 일정의 14일 rolling occurrence를 같은 DB transaction에서 생성한다."""

    def __init__(
        self,
        repository: MedicationScheduleRepository,
        *,
        service_timezone: tzinfo,
    ) -> None:
        _require_asia_seoul(service_timezone)
        self._repository = repository
        self._timezone = service_timezone

    async def generate(self, *, now: datetime) -> RollingOccurrenceGenerationResult:
        now_utc = _aware_instant(now, field="now")
        horizon_start = now_utc.astimezone(self._timezone).date()
        horizon_end = horizon_start + timedelta(days=ROLLING_HORIZON_DAYS - 1)

        locked_schedule_ids = await self._repository.lock_schedule_graph()
        ended_schedule_ids = await self._repository.mark_expired_schedules_ended(
            local_date=horizon_start, effective_at=now_utc, locked_schedule_ids=locked_schedule_ids
        )
        targets = await self._repository.list_generation_targets_for_update(
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            locked_schedule_ids=locked_schedule_ids,
        )

        created_count = 0
        duplicate_count = 0
        for schedule, schedule_time in targets:
            lower_bound = await self._repository.generation_lower_bound(schedule)
            first_date = max(horizon_start, schedule.start_local_date)
            last_date = min(horizon_end, schedule.end_local_date or horizon_end)
            if first_date > last_date:
                continue

            for local_date in _inclusive_dates(first_date, last_date):
                scheduled_local = datetime.combine(local_date, schedule_time.local_time, tzinfo=self._timezone)
                if lower_bound is not None and scheduled_local < lower_bound:
                    continue
                next_midnight = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=self._timezone)
                confirmation_deadline = max(next_midnight, scheduled_local + timedelta(hours=4))
                occurrence_id = await self._repository.create_occurrence_if_absent(
                    schedule=schedule,
                    schedule_time=schedule_time,
                    scheduled_local_date=local_date,
                    scheduled_at=scheduled_local.astimezone(UTC),
                    confirmation_deadline_at=confirmation_deadline.astimezone(UTC),
                )
                if occurrence_id is None:
                    duplicate_count += 1
                else:
                    created_count += 1

        return RollingOccurrenceGenerationResult(
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            created_count=created_count,
            duplicate_count=duplicate_count,
            ended_schedule_count=len(ended_schedule_ids),
        )


class PrescriptionVersionMedicationInvalidationService:
    """처방 Version 변경 시 Track B·B5 취소를 같은 transaction에서 조합한다."""

    def __init__(
        self,
        repository: MedicationScheduleRepository,
        *,
        notification_cancellation: UndeliveredMedicationNotificationCancellationPort | None = None,
    ) -> None:
        self._repository = repository
        self._notification_cancellation = notification_cancellation

    async def cancel_future_for_prescription_version(
        self,
        *,
        prescription_version_id: UUID,
        effective_at: datetime,
    ) -> Sequence[UUID]:
        occurrence_ids = await self._repository.cancel_future_for_prescription_version(
            prescription_version_id=prescription_version_id,
            effective_at=effective_at,
        )
        if occurrence_ids and self._notification_cancellation is not None:
            await self._notification_cancellation.cancel_undelivered_for_occurrences(
                occurrence_ids=occurrence_ids,
                cancelled_at=as_utc_instant(effective_at, field="effective_at"),
            )
        return occurrence_ids
