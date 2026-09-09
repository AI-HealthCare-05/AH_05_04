from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.medication_schedules import (
    CheckinAudit,
    MedicationCheckinStatus,
    MedicationOccurrenceStatus,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
)
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.services.medication_checkins import MedicationCheckinDeadlineScheduler, MedicationCheckinService
from app.tests.repositories.test_medication_schedule_repository_integration import (
    _create_active_version_medication,
    _create_user_with_self_profile,
)


async def _create_occurrence(session: AsyncSession, *, owner, profile, deadline_at: datetime):
    _, medication = await _create_active_version_medication(session, owner=owner, profile=profile)
    schedules = MedicationScheduleRepository(session)
    schedule = await schedules.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 9),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert schedule is not None
    schedule_time = (
        await schedules.add_schedule_times(
            schedule=schedule,
            schedule_revision=schedule.revision,
            local_times=[time(9)],
        )
    )[0]
    return await schedules.create_occurrence(
        schedule=schedule,
        schedule_time=schedule_time,
        scheduled_local_date=date(2026, 9, 9),
        scheduled_at=deadline_at - timedelta(hours=4),
        confirmation_deadline_at=deadline_at,
    )


async def test_create_correct_audit_backlog_and_ownership_are_persisted(db_session: AsyncSession) -> None:
    owner, profile = await _create_user_with_self_profile(db_session, label="checkin-owner")
    intruder, _ = await _create_user_with_self_profile(db_session, label="checkin-intruder")
    occurrence = await _create_occurrence(
        db_session,
        owner=owner,
        profile=profile,
        deadline_at=datetime(2026, 9, 9, 4, tzinfo=UTC),
    )
    repository = MedicationCheckinRepository(db_session)
    invalidation = AsyncMock()
    service = MedicationCheckinService(repository, revision_invalidation=invalidation)

    generated = await MedicationCheckinDeadlineScheduler(repository).generate_unconfirmed(
        now=datetime(2026, 9, 9, 4, tzinfo=UTC)
    )
    await db_session.flush()

    assert generated.processed_count == 1
    assert occurrence.status == MedicationOccurrenceStatus.CLOSED
    backlog = await service.list_unconfirmed_owned(user_id=owner.id)
    assert len(backlog) == 1
    assert await service.list_unconfirmed_owned(user_id=intruder.id) == ()

    with pytest.raises(ApiError) as hidden:
        await service.put_owned(
            occurrence_id=occurrence.id,
            user_id=intruder.id,
            status=MedicationCheckinStatus.TAKEN,
            taken_at=None,
            expected_revision=1,
        )
    assert hidden.value.status_code == 404

    corrected = await service.put_owned(
        occurrence_id=occurrence.id,
        user_id=owner.id,
        status=MedicationCheckinStatus.NOT_TAKEN,
        taken_at=None,
        expected_revision=1,
        changed_at=datetime(2026, 9, 9, 5, tzinfo=UTC),
    )
    assert corrected.revision == 2
    assert corrected.status == MedicationCheckinStatus.NOT_TAKEN

    corrected_again = await service.put_owned(
        occurrence_id=occurrence.id,
        user_id=owner.id,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=datetime(2026, 9, 9, 6, tzinfo=UTC),
        expected_revision=2,
        changed_at=datetime(2026, 9, 9, 6, tzinfo=UTC),
    )
    assert corrected_again.revision == 3
    invalidation.invalidate_for_checkin_revision.assert_awaited_once_with(
        checkin_id=corrected.checkin_id,
        invalidated_revision=2,
        invalidated_at=datetime(2026, 9, 9, 6, tzinfo=UTC),
    )

    audits = list(
        (
            await db_session.execute(
                select(CheckinAudit)
                .where(CheckinAudit.checkin_id == corrected.checkin_id)
                .order_by(CheckinAudit.to_revision)
            )
        )
        .scalars()
        .all()
    )
    assert [(row.from_status, row.to_status, row.from_revision, row.to_revision) for row in audits] == [
        (MedicationCheckinStatus.UNCONFIRMED, MedicationCheckinStatus.NOT_TAKEN, 1, 2),
        (MedicationCheckinStatus.NOT_TAKEN, MedicationCheckinStatus.TAKEN, 2, 3),
    ]
    assert all(row.changed_by == owner.id for row in audits)


async def test_duplicate_first_write_returns_revision_conflict(db_session: AsyncSession) -> None:
    owner, profile = await _create_user_with_self_profile(db_session, label="checkin-duplicate")
    occurrence = await _create_occurrence(
        db_session,
        owner=owner,
        profile=profile,
        deadline_at=datetime(2026, 9, 9, 4, tzinfo=UTC),
    )
    service = MedicationCheckinService(
        MedicationCheckinRepository(db_session),
        revision_invalidation=AsyncMock(),
    )
    await service.put_owned(
        occurrence_id=occurrence.id,
        user_id=owner.id,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=None,
        expected_revision=0,
    )

    with pytest.raises(ApiError) as conflict:
        await service.put_owned(
            occurrence_id=occurrence.id,
            user_id=owner.id,
            status=MedicationCheckinStatus.NOT_TAKEN,
            taken_at=None,
            expected_revision=0,
        )

    assert conflict.value.status_code == 409
    assert conflict.value.code == "CHECKIN_REVISION_CONFLICT"
