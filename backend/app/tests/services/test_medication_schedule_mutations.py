from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_schedule_snapshots import ScheduleAuditSnapshot
from app.models.medication_schedules import (
    MedicationOccurrence,
    MedicationOccurrenceStatus,
    MedicationScheduleAudit,
    MedicationScheduleTime,
)
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.services.medication_occurrences import MedicationOccurrenceScheduler
from app.services.medication_schedule_mutations import (
    MedicationScheduleMutationService,
    ScheduleRevisionConflictError,
)
from app.tests.repositories.test_medication_schedule_repository_integration import (
    _create_active_version_medication,
    _create_user_with_self_profile,
)


def _settings() -> ScheduleAuditSnapshot:
    return ScheduleAuditSnapshot.model_validate(
        {
            "start_local_date": "2026-09-10",
            "end_mode": "DATE",
            "end_local_date": "2026-09-11",
            "local_times": ["09:00"],
            "status": "ACTIVE",
            "source": "USER_CONFIRMED",
        }
    )


async def _fixture(session: AsyncSession):
    owner, profile = await _create_user_with_self_profile(session, label="audit")
    _, medication = await _create_active_version_medication(session, owner=owner, profile=profile)
    repository = MedicationScheduleRepository(session)
    cancellation = AsyncMock()
    return (
        owner,
        medication,
        repository,
        MedicationScheduleMutationService(repository, notification_cancellation=cancellation),
        cancellation,
    )


async def test_put_cancel_noop_reactivate_preserves_times_and_audit(db_session: AsyncSession) -> None:
    owner, medication, _, service, cancellation = await _fixture(db_session)
    now = datetime(2026, 9, 10, 0, tzinfo=UTC)
    schedule = await service.put(
        medication_id=medication.id,
        user_id=owner.id,
        expected_revision=0,
        settings=_settings(),
        effective_at=now,
    )
    original_time_ids = set((await db_session.scalars(select(MedicationScheduleTime.id))).all())
    assert schedule.revision == 1
    await service.cancel(medication_id=medication.id, user_id=owner.id, expected_revision=1, effective_at=now)
    assert schedule.revision == 2
    cancelled = list((await db_session.scalars(select(MedicationOccurrence))).all())
    assert all(row.cancelled_at == now and row.status == MedicationOccurrenceStatus.CANCELLED for row in cancelled)
    cancellation.cancel_undelivered_for_occurrences.assert_awaited_once()
    await service.cancel(medication_id=medication.id, user_id=owner.id, expected_revision=2, effective_at=now)
    assert schedule.revision == 2
    await service.put(
        medication_id=medication.id,
        user_id=owner.id,
        expected_revision=2,
        settings=_settings(),
        effective_at=now,
    )
    assert schedule.revision == 3
    assert original_time_ids <= set((await db_session.scalars(select(MedicationScheduleTime.id))).all())
    audits = list(
        (await db_session.scalars(select(MedicationScheduleAudit).order_by(MedicationScheduleAudit.to_revision))).all()
    )
    assert [(row.from_revision, row.to_revision) for row in audits] == [(0, 1), (1, 2), (2, 3)]
    assert audits[0].before_snapshot is None
    assert audits[1].after_snapshot["local_times"] == ["09:00"]
    assert audits[2].before_snapshot == audits[1].after_snapshot


async def test_new_key_stale_revision_does_not_append_audit(db_session: AsyncSession) -> None:
    owner, medication, _, service, _ = await _fixture(db_session)
    now = datetime(2026, 9, 10, 0, tzinfo=UTC)
    await service.put(
        medication_id=medication.id,
        user_id=owner.id,
        expected_revision=0,
        settings=_settings(),
        effective_at=now,
    )
    with pytest.raises(ScheduleRevisionConflictError):
        await service.put(
            medication_id=medication.id,
            user_id=owner.id,
            expected_revision=0,
            settings=_settings(),
            effective_at=now,
        )
    assert await db_session.scalar(select(func.count()).select_from(MedicationScheduleAudit)) == 1


async def test_rolling_does_not_backfill_before_current_user_audit(db_session: AsyncSession) -> None:
    from zoneinfo import ZoneInfo

    owner, medication, repository, service, _ = await _fixture(db_session)
    now = datetime(2026, 9, 10, 0, 1, tzinfo=UTC)
    await service.put(
        medication_id=medication.id,
        user_id=owner.id,
        expected_revision=0,
        settings=_settings(),
        effective_at=now,
    )
    scheduler = MedicationOccurrenceScheduler(repository, service_timezone=ZoneInfo("Asia/Seoul"))
    await scheduler.generate(now=now)
    occurrences = list((await db_session.scalars(select(MedicationOccurrence))).all())
    assert len(occurrences) == 1
    assert occurrences[0].scheduled_local_date == date(2026, 9, 11)


async def test_scheduler_ends_once_and_preserves_time_snapshot(db_session: AsyncSession) -> None:
    owner, medication, repository, service, _ = await _fixture(db_session)
    now = datetime(2026, 9, 10, 0, tzinfo=UTC)
    schedule = await service.put(
        medication_id=medication.id,
        user_id=owner.id,
        expected_revision=0,
        settings=_settings(),
        effective_at=now,
    )
    ended_at = datetime(2026, 9, 11, 15, tzinfo=UTC)
    assert await repository.mark_expired_schedules_ended(local_date=date(2026, 9, 12), effective_at=ended_at) == (
        schedule.id,
    )
    assert await repository.mark_expired_schedules_ended(local_date=date(2026, 9, 12), effective_at=ended_at) == ()
    assert schedule.revision == 2
    audit = await db_session.scalar(select(MedicationScheduleAudit).where(MedicationScheduleAudit.to_revision == 2))
    assert audit is not None and audit.changed_by is None and audit.change_source == "SCHEDULER"
    assert audit.after_snapshot["local_times"] == ["09:00"]
    assert await db_session.scalar(select(func.count()).select_from(MedicationOccurrence)) == 2


async def test_notification_failure_rolls_back_schedule_audit_time_and_occurrences(db_session: AsyncSession) -> None:
    owner, medication, _, service, cancellation = await _fixture(db_session)
    now = datetime(2026, 9, 10, 0, tzinfo=UTC)
    schedule = await service.put(
        medication_id=medication.id,
        user_id=owner.id,
        expected_revision=0,
        settings=_settings(),
        effective_at=now,
    )
    cancellation.cancel_undelivered_for_occurrences.side_effect = RuntimeError("synthetic cancellation failure")
    with pytest.raises(RuntimeError, match="synthetic cancellation failure"):
        async with db_session.begin_nested():
            await service.put(
                medication_id=medication.id,
                user_id=owner.id,
                expected_revision=1,
                settings=_settings(),
                effective_at=now,
            )
    await db_session.refresh(schedule)
    assert schedule.revision == 1
    assert await db_session.scalar(select(func.count()).select_from(MedicationScheduleAudit)) == 1
    assert await db_session.scalar(select(func.count()).select_from(MedicationScheduleTime)) == 1
    occurrences = list((await db_session.scalars(select(MedicationOccurrence))).all())
    assert len(occurrences) == 2
    assert all(row.status == MedicationOccurrenceStatus.PENDING and row.cancelled_at is None for row in occurrences)


async def test_audit_failure_rolls_back_schedule_and_new_times(db_session: AsyncSession, monkeypatch) -> None:
    from app.models.medication_schedules import MedicationSchedule

    owner, medication, repository, service, _ = await _fixture(db_session)
    monkeypatch.setattr(repository, "append_audit", AsyncMock(side_effect=RuntimeError("synthetic audit failure")))
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        await service.put(
            medication_id=medication.id,
            user_id=owner.id,
            expected_revision=0,
            settings=_settings(),
            effective_at=datetime(2026, 9, 10, 0, tzinfo=UTC),
        )
    for model in (MedicationSchedule, MedicationScheduleTime, MedicationScheduleAudit, MedicationOccurrence):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("competitor", ["scheduler", "put"])
async def test_concurrent_end_and_write_use_one_ordered_revision_chain(competitor: str) -> None:
    import asyncio

    from sqlalchemy import delete

    from app.models.medication_schedules import MedicationSchedule
    from app.tests.conftest import test_engine
    from app.tests.repositories.test_medication_schedule_repository_integration import _delete_committed_fixture

    async with AsyncSession(test_engine, expire_on_commit=False) as setup:
        owner, profile = await _create_user_with_self_profile(setup, label="race")
        prescription, medication = await _create_active_version_medication(setup, owner=owner, profile=profile)
        service = MedicationScheduleMutationService(
            MedicationScheduleRepository(setup), notification_cancellation=AsyncMock()
        )
        schedule = await service.put(
            medication_id=medication.id,
            user_id=owner.id,
            expected_revision=0,
            settings=_settings(),
            effective_at=datetime(2026, 9, 10, tzinfo=UTC),
        )
        await setup.commit()
        ids = dict(
            owner_id=owner.id,
            profile_id=profile.id,
            document_id=prescription.document_id,
            ocr_job_id=prescription.source_ocr_job_id,
            prescription_id=prescription.id,
            schedule_id=schedule.id,
        )
    barrier = asyncio.Barrier(2)
    ended_at = datetime(2026, 9, 11, 15, tzinfo=UTC)

    async def run(kind):
        async with AsyncSession(test_engine, expire_on_commit=False) as session:
            async with session.begin():
                repo = MedicationScheduleRepository(session)
                await barrier.wait()
                if kind == "scheduler":
                    return await repo.mark_expired_schedules_ended(local_date=date(2026, 9, 12), effective_at=ended_at)
                writer = MedicationScheduleMutationService(repo, notification_cancellation=AsyncMock())
                try:
                    await writer.put(
                        medication_id=medication.id,
                        user_id=owner.id,
                        expected_revision=1,
                        settings=_settings(),
                        effective_at=ended_at,
                    )
                    return "put"
                except ScheduleRevisionConflictError:
                    return "stale"

    try:
        results = await asyncio.wait_for(asyncio.gather(run("scheduler"), run(competitor)), timeout=10)
        async with AsyncSession(test_engine) as session:
            row = await session.get(MedicationSchedule, schedule.id)
            audits = list(
                (
                    await session.scalars(
                        select(MedicationScheduleAudit)
                        .where(MedicationScheduleAudit.medication_schedule_id == schedule.id)
                        .order_by(MedicationScheduleAudit.to_revision)
                    )
                ).all()
            )
            assert row is not None and row.status == "ENDED"
            assert [a.to_revision for a in audits] == list(range(1, row.revision + 1))
            assert sum(a.change_source == "SCHEDULER" for a in audits) == 1
            if competitor == "scheduler":
                assert sum(len(result) for result in results) == 1 and row.revision == 2
    finally:
        async with AsyncSession(test_engine) as session:
            await session.execute(
                delete(MedicationScheduleAudit).where(MedicationScheduleAudit.medication_schedule_id == schedule.id)
            )
            await _delete_committed_fixture(session, **ids)


@pytest.mark.parametrize("cap_failure", [False, True])
async def test_real_idempotency_service_replays_or_rolls_back_storage(
    db_session: AsyncSession, monkeypatch, cap_failure
) -> None:
    from app.models.async_jobs import IdempotencyRecord
    from app.models.medication_schedules import MedicationSchedule
    from app.repositories.idempotency_repository import IdempotencyRepository
    from app.services.idempotency import (
        IdempotencyResponseTooLargeError,
        SyncMutationIdempotencyService,
        get_default_snapshot_cipher,
    )

    owner, medication, _, writer, _ = await _fixture(db_session)
    service = SyncMutationIdempotencyService(IdempotencyRepository(db_session), get_default_snapshot_cipher())
    calls = 0

    async def mutate():
        nonlocal calls
        calls += 1
        schedule = await writer.put(
            medication_id=medication.id,
            user_id=owner.id,
            expected_revision=0,
            settings=_settings(),
            effective_at=datetime(2026, 9, 10, tzinfo=UTC),
        )
        return {"id": str(schedule.id), "revision": schedule.revision}

    kwargs = dict(
        user_id=owner.id,
        operation_id="schedule.put",
        parent_resource_id=medication.id,
        idempotency_key="synthetic-schedule-key",
        fingerprint={"expected_revision": 0},
        success_status=200,
        mutate=mutate,
    )
    if cap_failure:
        monkeypatch.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)
        with pytest.raises(IdempotencyResponseTooLargeError):
            await service.execute(**kwargs)
        for model in (
            MedicationSchedule,
            MedicationScheduleAudit,
            MedicationScheduleTime,
            MedicationOccurrence,
            IdempotencyRecord,
        ):
            assert await db_session.scalar(select(func.count()).select_from(model)) == 0
    else:
        first = await service.execute(**kwargs)
        replay = await service.execute(**kwargs)
        assert calls == 1 and replay.is_replay and first.response_body == replay.response_body
        assert await db_session.scalar(select(func.count()).select_from(MedicationScheduleAudit)) == 1


async def test_same_session_notification_write_rolls_back_with_snapshot_cap(
    db_session: AsyncSession, monkeypatch
) -> None:
    from sqlalchemy import text

    from app.repositories.idempotency_repository import IdempotencyRepository
    from app.services.idempotency import (
        IdempotencyResponseTooLargeError,
        SyncMutationIdempotencyService,
        get_default_snapshot_cipher,
    )

    owner, medication, repository, writer, _ = await _fixture(db_session)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    schedule = await writer.put(
        medication_id=medication.id, user_id=owner.id, expected_revision=0, settings=_settings(), effective_at=now
    )
    # A test-only SQL adapter checks the transaction boundary without defining #203's schema.
    await db_session.execute(
        text("CREATE TEMP TABLE synthetic_notification_cancellation (cancelled boolean) ON COMMIT DROP")
    )
    await db_session.execute(text("INSERT INTO synthetic_notification_cancellation VALUES (false)"))

    class Cancellation:
        async def cancel_undelivered_for_occurrences(self, *, occurrence_ids, cancelled_at):
            assert occurrence_ids and cancelled_at == now
            await db_session.execute(text("UPDATE synthetic_notification_cancellation SET cancelled=true"))

    writer = MedicationScheduleMutationService(repository, notification_cancellation=Cancellation())
    idempotency = SyncMutationIdempotencyService(IdempotencyRepository(db_session), get_default_snapshot_cipher())
    monkeypatch.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)

    async def mutate():
        result = await writer.cancel(
            medication_id=medication.id, user_id=owner.id, expected_revision=1, effective_at=now
        )
        return {"revision": result.revision}

    with pytest.raises(IdempotencyResponseTooLargeError):
        await idempotency.execute(
            user_id=owner.id,
            operation_id="schedule.patch",
            parent_resource_id=medication.id,
            idempotency_key="synthetic-cancel-key",
            fingerprint={"revision": 1},
            success_status=200,
            mutate=mutate,
        )
    await db_session.refresh(schedule)
    assert schedule.revision == 1
    assert await db_session.scalar(text("SELECT cancelled FROM synthetic_notification_cancellation")) is False
    assert await db_session.scalar(select(func.count()).select_from(MedicationScheduleAudit)) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(MedicationOccurrence).where(MedicationOccurrence.status == "CANCELLED")
        )
        == 0
    )


async def test_ownership_and_inactive_version_do_not_mutate(db_session: AsyncSession) -> None:
    from uuid import uuid4

    from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
    from app.services.medication_schedule_mutations import ScheduleOwnershipNotFoundError, ScheduleVersionConflictError
    from app.tests.fixtures.prescription_fingerprint import fingerprint_values

    owner, medication, _, writer, _ = await _fixture(db_session)
    intruder, _ = await _create_user_with_self_profile(db_session, label="intruder")
    args = dict(
        medication_id=medication.id,
        expected_revision=0,
        settings=_settings(),
        effective_at=datetime(2026, 9, 10, tzinfo=UTC),
    )
    with pytest.raises(ScheduleOwnershipNotFoundError):
        await writer.put(user_id=intruder.id, **args)
    prescription = await db_session.scalar(select(Prescription))
    assert prescription is not None
    replacement = PrescriptionVersion(
        id=uuid4(),
        prescription_id=prescription.id,
        version_number=2,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=prescription.confirmed_at,
        **fingerprint_values(
            prescription.prescribed_date,
            [{"medication_name": "합성테스트약", "frequency_per_day": 1, "display_order": 1}],
        ),
    )
    db_session.add(replacement)
    await db_session.flush()
    db_session.add(
        PrescriptionVersionMedication(
            prescription_version_id=replacement.id,
            medication_count=1,
            medication_name="합성테스트약",
            frequency_per_day=1,
            display_order=1,
        )
    )
    prescription.active_version_id = replacement.id
    await db_session.flush()
    with pytest.raises(ScheduleVersionConflictError):
        await writer.put(user_id=owner.id, **args)
    assert await db_session.scalar(select(func.count()).select_from(MedicationScheduleAudit)) == 0


async def test_legacy_baseline_starts_audit_at_next_real_revision(db_session: AsyncSession) -> None:
    from datetime import time

    from app.models.medication_schedules import MedicationScheduleEndMode, MedicationScheduleSource

    owner, medication, repository, writer, _ = await _fixture(db_session)
    legacy = await repository.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 10),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
        revision=5,
    )
    times = await repository.add_schedule_times(schedule=legacy, schedule_revision=5, local_times=[time(9)])
    now = datetime(2026, 9, 10, 0, 1, tzinfo=UTC)
    result = await writer.put(
        medication_id=medication.id, user_id=owner.id, expected_revision=5, settings=_settings(), effective_at=now
    )
    audit = await db_session.scalar(select(MedicationScheduleAudit))
    assert result.id == legacy.id and result.revision == 6
    assert audit is not None
    assert audit.from_revision == 5 and audit.to_revision == 6 and audit.changed_by == owner.id
    assert await db_session.scalar(select(func.count()).select_from(MedicationScheduleAudit)) == 1
    assert await db_session.get(MedicationScheduleTime, times[0].id) is not None
    with pytest.raises(ValueError, match="revision must match"):
        await repository.create_occurrence_if_absent(
            schedule=result,
            schedule_time=times[0],
            scheduled_local_date=date(2026, 9, 11),
            scheduled_at=datetime(2026, 9, 11, tzinfo=UTC),
            confirmation_deadline_at=datetime(2026, 9, 12, tzinfo=UTC),
        )


async def test_checkin_before_cancel_is_preserved_at_effective_boundary(db_session: AsyncSession) -> None:
    from app.models.medication_schedules import MedicationCheckin, MedicationCheckinStatus
    from app.repositories.medication_checkin_repository import MedicationCheckinRepository
    from app.services.medication_checkins import MedicationCheckinService

    owner, medication, _, writer, _ = await _fixture(db_session)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    await writer.put(
        medication_id=medication.id, user_id=owner.id, expected_revision=0, settings=_settings(), effective_at=now
    )
    occurrences = list(
        (await db_session.scalars(select(MedicationOccurrence).order_by(MedicationOccurrence.scheduled_at))).all()
    )
    first = occurrences[0]
    checkins = MedicationCheckinService(MedicationCheckinRepository(db_session), revision_invalidation=AsyncMock())
    await checkins.put_owned(
        occurrence_id=first.id,
        user_id=owner.id,
        status=MedicationCheckinStatus.TAKEN,
        taken_at=now,
        expected_revision=0,
        changed_at=now,
    )
    await writer.cancel(medication_id=medication.id, user_id=owner.id, expected_revision=1, effective_at=now)
    await db_session.refresh(first)
    assert first.status == MedicationOccurrenceStatus.CLOSED and first.cancelled_at is None
    assert await db_session.scalar(select(func.count()).select_from(MedicationCheckin)) == 1
    assert occurrences[1].status == MedicationOccurrenceStatus.CANCELLED
