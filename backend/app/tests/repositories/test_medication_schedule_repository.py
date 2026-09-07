from datetime import UTC, date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.medication_schedules import (
    MedicationOccurrence,
    MedicationSchedule,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
    MedicationScheduleTime,
)
from app.repositories.medication_schedule_repository import MedicationScheduleRepository, as_utc_instant


def _repository(*, owned: bool = True) -> tuple[MedicationScheduleRepository, Mock, AsyncMock]:
    session = Mock()
    session.flush = AsyncMock()
    ownership = AsyncMock()
    ownership.is_owned.return_value = owned
    return MedicationScheduleRepository(session, ownership=ownership), session, ownership


def _schedule() -> MedicationSchedule:
    return MedicationSchedule(
        id=uuid4(),
        prescription_version_medication_id=uuid4(),
        start_local_date=date(2026, 9, 7),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
        revision=1,
    )


def test_as_utc_instant_converts_an_aware_kst_value() -> None:
    kst = timezone(timedelta(hours=9))

    result = as_utc_instant(datetime(2026, 9, 7, 9, 30, tzinfo=kst), field="scheduled_at")

    assert result == datetime(2026, 9, 7, 0, 30, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_as_utc_instant_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        as_utc_instant(datetime(2026, 9, 7, 9, 30), field="scheduled_at")


async def test_owned_schedule_is_fail_closed_by_issue_169_boundary() -> None:
    repository, session, ownership = _repository(owned=False)
    schedule = _schedule()
    session.get = AsyncMock(return_value=schedule)
    user_id = uuid4()

    result = await repository.get_schedule_owned(schedule_id=schedule.id, user_id=user_id)

    assert result is None
    ownership.is_owned.assert_awaited_once_with(
        prescription_version_medication_id=schedule.prescription_version_medication_id,
        user_id=user_id,
    )


async def test_create_schedule_is_fail_closed_by_issue_169_boundary() -> None:
    repository, session, ownership = _repository(owned=False)
    medication_id = uuid4()
    user_id = uuid4()

    result = await repository.create_schedule_owned(
        prescription_version_medication_id=medication_id,
        user_id=user_id,
        start_local_date=date(2026, 9, 7),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )

    assert result is None
    ownership.is_owned.assert_awaited_once_with(
        prescription_version_medication_id=medication_id,
        user_id=user_id,
    )
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


async def test_owned_occurrence_is_returned_only_after_parent_schedule_ownership() -> None:
    repository, session, ownership = _repository(owned=True)
    schedule = _schedule()
    occurrence = MedicationOccurrence(
        id=uuid4(),
        medication_schedule_id=schedule.id,
        medication_schedule_time_id=uuid4(),
        schedule_revision=1,
        scheduled_local_date=date(2026, 9, 7),
        scheduled_at=datetime(2026, 9, 7, tzinfo=UTC),
        confirmation_deadline_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    session.get = AsyncMock(side_effect=[occurrence, schedule])
    user_id = uuid4()

    result = await repository.get_occurrence_owned(occurrence_id=occurrence.id, user_id=user_id)

    assert result is occurrence
    ownership.is_owned.assert_awaited_once_with(
        prescription_version_medication_id=schedule.prescription_version_medication_id,
        user_id=user_id,
    )


async def test_create_occurrence_normalizes_kst_instants_to_utc() -> None:
    repository, session, _ = _repository()
    schedule = _schedule()
    schedule_time = MedicationScheduleTime(
        id=uuid4(),
        medication_schedule_id=schedule.id,
        schedule_revision=1,
        local_time=time(9, 30),
    )
    kst = timezone(timedelta(hours=9))

    occurrence = await repository.create_occurrence(
        schedule=schedule,
        schedule_time=schedule_time,
        scheduled_local_date=date(2026, 9, 7),
        scheduled_at=datetime(2026, 9, 7, 9, 30, tzinfo=kst),
        confirmation_deadline_at=datetime(2026, 9, 8, 0, 0, tzinfo=kst),
    )

    assert occurrence.scheduled_at == datetime(2026, 9, 7, 0, 30, tzinfo=UTC)
    assert occurrence.confirmation_deadline_at == datetime(2026, 9, 7, 15, 0, tzinfo=UTC)
    session.add.assert_called_once_with(occurrence)
    session.flush.assert_awaited_once()


async def test_create_occurrence_rejects_a_schedule_time_from_another_schedule() -> None:
    repository, session, _ = _repository()
    schedule = _schedule()
    schedule_time = MedicationScheduleTime(
        id=uuid4(),
        medication_schedule_id=uuid4(),
        schedule_revision=1,
        local_time=time(9, 30),
    )

    with pytest.raises(ValueError, match="must belong"):
        await repository.create_occurrence(
            schedule=schedule,
            schedule_time=schedule_time,
            scheduled_local_date=date(2026, 9, 7),
            scheduled_at=datetime(2026, 9, 7, tzinfo=UTC),
            confirmation_deadline_at=datetime(2026, 9, 8, tzinfo=UTC),
        )

    session.add.assert_not_called()
