from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.models.medication_schedules import MedicationCheckinStatus, MedicationOccurrenceStatus
from app.services.medication_checkins import MedicationCheckinDeadlineScheduler, MedicationCheckinService


def _service(repository: AsyncMock, invalidation: AsyncMock | None = None) -> MedicationCheckinService:
    return MedicationCheckinService(
        repository,
        revision_invalidation=invalidation or AsyncMock(),
    )


async def test_first_user_checkin_is_created_once_and_closes_occurrence() -> None:
    occurrence = SimpleNamespace(id=uuid4(), status=MedicationOccurrenceStatus.PENDING)
    created = SimpleNamespace(
        id=uuid4(),
        occurrence_id=occurrence.id,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=datetime(2026, 9, 9, 1, tzinfo=UTC),
        revision=1,
    )
    repository = AsyncMock()
    repository.lock_occurrence_owned.return_value = occurrence
    repository.get_current_for_update.return_value = None
    repository.create_if_absent.return_value = created

    result = await _service(repository).put_owned(
        occurrence_id=occurrence.id,
        user_id=uuid4(),
        status=MedicationCheckinStatus.TAKEN,
        taken_at=datetime(2026, 9, 9, 10, tzinfo=timezone(timedelta(hours=9))),
        expected_revision=0,
    )

    assert result.revision == 1
    assert result.corrected is False
    repository.create_if_absent.assert_awaited_once()
    repository.close_occurrence.assert_awaited_once_with(occurrence=occurrence)


@pytest.mark.parametrize(
    ("status", "taken_at", "code"),
    [
        (MedicationCheckinStatus.UNCONFIRMED, None, "CHECKIN_STATUS_NOT_USER_SETTABLE"),
        (MedicationCheckinStatus.NOT_TAKEN, datetime(2026, 9, 9, tzinfo=UTC), "VALIDATION_FAILED"),
        (MedicationCheckinStatus.TAKEN, datetime(2026, 9, 9), "VALIDATION_FAILED"),
    ],
)
async def test_invalid_user_states_are_rejected_before_storage(
    status: MedicationCheckinStatus,
    taken_at: datetime | None,
    code: str,
) -> None:
    repository = AsyncMock()

    with pytest.raises(ApiError) as caught:
        await _service(repository).put_owned(
            occurrence_id=uuid4(),
            user_id=uuid4(),
            status=status,
            taken_at=taken_at,
            expected_revision=0,
        )

    assert caught.value.status_code == 422
    assert caught.value.code == code
    repository.lock_occurrence_owned.assert_not_awaited()


async def test_missing_or_other_users_occurrence_is_hidden_as_404() -> None:
    repository = AsyncMock()
    repository.lock_occurrence_owned.return_value = None

    with pytest.raises(ApiError) as caught:
        await _service(repository).put_owned(
            occurrence_id=uuid4(),
            user_id=uuid4(),
            status=MedicationCheckinStatus.NOT_TAKEN,
            taken_at=None,
            expected_revision=0,
        )

    assert caught.value.status_code == 404
    assert caught.value.code == "MEDICATION_OCCURRENCE_NOT_FOUND"


async def test_cancelled_occurrence_and_stale_revision_do_not_mutate() -> None:
    repository = AsyncMock()
    repository.lock_occurrence_owned.return_value = SimpleNamespace(
        id=uuid4(), status=MedicationOccurrenceStatus.CANCELLED
    )

    with pytest.raises(ApiError) as cancelled:
        await _service(repository).put_owned(
            occurrence_id=uuid4(),
            user_id=uuid4(),
            status=MedicationCheckinStatus.NOT_TAKEN,
            taken_at=None,
            expected_revision=0,
        )
    assert cancelled.value.code == "OCCURRENCE_CANCELLED"

    repository.lock_occurrence_owned.return_value = SimpleNamespace(
        id=uuid4(), status=MedicationOccurrenceStatus.CLOSED
    )
    repository.get_current_for_update.return_value = SimpleNamespace(revision=2)
    with pytest.raises(ApiError) as conflict:
        await _service(repository).put_owned(
            occurrence_id=uuid4(),
            user_id=uuid4(),
            status=MedicationCheckinStatus.TAKEN,
            taken_at=None,
            expected_revision=1,
        )
    assert conflict.value.code == "CHECKIN_REVISION_CONFLICT"
    repository.correct.assert_not_awaited()


async def test_correction_appends_audit_and_invalidates_previous_not_taken_revision() -> None:
    user_id = uuid4()
    occurrence = SimpleNamespace(id=uuid4(), status=MedicationOccurrenceStatus.CLOSED)
    checkin = SimpleNamespace(
        id=uuid4(),
        occurrence_id=occurrence.id,
        status=MedicationCheckinStatus.NOT_TAKEN,
        taken_at=None,
        revision=1,
    )
    repository = AsyncMock()
    repository.lock_occurrence_owned.return_value = occurrence
    repository.get_current_for_update.return_value = checkin

    async def correct(**kwargs: object) -> None:
        checkin.status = kwargs["status"]
        checkin.taken_at = kwargs["taken_at"]
        checkin.revision += 1

    repository.correct.side_effect = correct
    invalidation = AsyncMock()
    changed_at = datetime(2026, 9, 9, 2, tzinfo=UTC)

    result = await _service(repository, invalidation).put_owned(
        occurrence_id=occurrence.id,
        user_id=user_id,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=changed_at,
        expected_revision=1,
        changed_at=changed_at,
    )

    assert result.status == MedicationCheckinStatus.TAKEN
    assert result.revision == 2
    assert result.corrected is True
    repository.correct.assert_awaited_once_with(
        checkin=checkin,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=changed_at,
        changed_by=user_id,
        changed_at=changed_at,
    )
    invalidation.invalidate_for_checkin_revision.assert_awaited_once_with(
        checkin_id=checkin.id,
        invalidated_revision=1,
        invalidated_at=changed_at,
    )


async def test_deadline_scheduler_processes_locked_due_batch_and_is_collision_safe() -> None:
    first = SimpleNamespace(id=uuid4())
    raced = SimpleNamespace(id=uuid4())
    repository = AsyncMock()
    repository.list_due_occurrences_for_update.return_value = (first, raced)
    repository.create_if_absent.side_effect = [SimpleNamespace(id=uuid4()), None]
    now = datetime(2026, 9, 9, 3, tzinfo=UTC)

    result = await MedicationCheckinDeadlineScheduler(repository).generate_unconfirmed(
        now=now,
        batch_size=20,
    )

    assert result.processed_count == 1
    assert result.duplicate_count == 1
    repository.list_due_occurrences_for_update.assert_awaited_once_with(deadline_at=now, batch_size=20)
    repository.close_occurrence.assert_awaited_once_with(occurrence=first)


async def test_unconfirmed_backlog_is_returned_only_from_owned_repository_boundary() -> None:
    user_id = uuid4()
    checkin = SimpleNamespace(
        id=uuid4(),
        occurrence_id=uuid4(),
        status=MedicationCheckinStatus.UNCONFIRMED,
        taken_at=None,
        revision=1,
    )
    repository = AsyncMock()
    repository.list_unconfirmed_owned.return_value = (checkin,)

    result = await _service(repository).list_unconfirmed_owned(user_id=user_id, limit=25)

    assert result[0].status == MedicationCheckinStatus.UNCONFIRMED
    repository.list_unconfirmed_owned.assert_awaited_once_with(user_id=user_id, limit=25)
