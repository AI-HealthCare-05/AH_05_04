from datetime import UTC, date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.medication_schedules import (
    MedicationSchedule,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
    MedicationScheduleTime,
)
from app.services.medication_occurrences import (
    MedicationOccurrenceScheduler,
    PrescriptionVersionMedicationInvalidationService,
)


def _schedule(
    *,
    start_local_date: date,
    end_local_date: date | None = None,
) -> MedicationSchedule:
    return MedicationSchedule(
        id=uuid4(),
        prescription_version_medication_id=uuid4(),
        start_local_date=start_local_date,
        end_mode=(
            MedicationScheduleEndMode.DATE if end_local_date is not None else MedicationScheduleEndMode.OPEN_ENDED
        ),
        end_local_date=end_local_date,
        source=MedicationScheduleSource.USER_CONFIRMED,
        revision=1,
    )


def _schedule_time(schedule: MedicationSchedule, local_time: time) -> MedicationScheduleTime:
    return MedicationScheduleTime(
        id=uuid4(),
        medication_schedule_id=schedule.id,
        schedule_revision=schedule.revision,
        local_time=local_time,
    )


def test_scheduler_rejects_a_non_seoul_service_timezone() -> None:
    with pytest.raises(ValueError, match="TIMEZONE=Asia/Seoul"):
        MedicationOccurrenceScheduler(AsyncMock(), service_timezone=UTC)


async def test_scheduler_generates_fourteen_local_dates_and_deadline_snapshots() -> None:
    seoul = timezone(timedelta(hours=9), name="Asia/Seoul")
    repository = AsyncMock()
    repository.generation_lower_bound.return_value = None
    repository.mark_expired_schedules_ended.return_value = ()
    schedule = _schedule(start_local_date=date(2026, 9, 9))
    morning = _schedule_time(schedule, time(9, 0))
    late = _schedule_time(schedule, time(23, 0))
    repository.list_generation_targets_for_update.return_value = [(schedule, morning), (schedule, late)]
    repository.create_occurrence_if_absent.return_value = uuid4()
    scheduler = MedicationOccurrenceScheduler(repository, service_timezone=seoul)

    result = await scheduler.generate(now=datetime(2026, 9, 8, 15, 30, tzinfo=UTC))

    assert result.horizon_start == date(2026, 9, 9)
    assert result.horizon_end == date(2026, 9, 22)
    assert result.created_count == 28
    assert result.duplicate_count == 0
    calls = repository.create_occurrence_if_absent.await_args_list
    morning_first = calls[0].kwargs
    late_first = calls[14].kwargs
    assert morning_first["scheduled_local_date"] == date(2026, 9, 9)
    assert morning_first["scheduled_at"] == datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
    assert morning_first["confirmation_deadline_at"] == datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    assert late_first["scheduled_at"] == datetime(2026, 9, 9, 14, 0, tzinfo=UTC)
    assert late_first["confirmation_deadline_at"] == datetime(2026, 9, 9, 18, 0, tzinfo=UTC)


async def test_scheduler_clips_to_schedule_dates_and_counts_duplicates() -> None:
    seoul = timezone(timedelta(hours=9), name="Asia/Seoul")
    repository = AsyncMock()
    repository.generation_lower_bound.return_value = None
    repository.mark_expired_schedules_ended.return_value = (uuid4(),)
    schedule = _schedule(
        start_local_date=date(2026, 9, 11),
        end_local_date=date(2026, 9, 12),
    )
    repository.list_generation_targets_for_update.return_value = [(schedule, _schedule_time(schedule, time(8, 30)))]
    repository.create_occurrence_if_absent.side_effect = [uuid4(), None]
    scheduler = MedicationOccurrenceScheduler(repository, service_timezone=seoul)

    result = await scheduler.generate(now=datetime(2026, 9, 9, tzinfo=seoul))

    assert result.created_count == 1
    assert result.duplicate_count == 1
    assert result.ended_schedule_count == 1
    assert [call.kwargs["scheduled_local_date"] for call in repository.create_occurrence_if_absent.await_args_list] == [
        date(2026, 9, 11),
        date(2026, 9, 12),
    ]


async def test_version_invalidation_calls_b5_port_with_cancelled_occurrences() -> None:
    repository = AsyncMock()
    repository.generation_lower_bound.return_value = None
    notification_cancellation = AsyncMock()
    occurrence_ids = (uuid4(), uuid4())
    repository.cancel_future_for_prescription_version.return_value = occurrence_ids
    service = PrescriptionVersionMedicationInvalidationService(
        repository,
        notification_cancellation=notification_cancellation,
    )
    effective_at = datetime(2026, 9, 9, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    prescription_version_id = uuid4()

    result = await service.cancel_future_for_prescription_version(
        prescription_version_id=prescription_version_id,
        effective_at=effective_at,
    )

    assert result == occurrence_ids
    repository.cancel_future_for_prescription_version.assert_awaited_once_with(
        prescription_version_id=prescription_version_id,
        effective_at=effective_at,
    )
    notification_cancellation.cancel_undelivered_for_occurrences.assert_awaited_once_with(
        occurrence_ids=occurrence_ids,
        cancelled_at=datetime(2026, 9, 9, 0, 0, tzinfo=UTC),
    )
