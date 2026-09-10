import asyncio
from datetime import UTC, date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_documents import MedicalDocument
from app.models.medication_schedules import (
    MedicationOccurrence,
    MedicationOccurrenceStatus,
    MedicationSchedule,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
    MedicationScheduleStatus,
    MedicationScheduleTime,
)
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.services.medication_occurrences import MedicationOccurrenceScheduler
from app.tests.conftest import test_engine
from app.tests.fixtures.prescription_fingerprint import fingerprint_values


async def _create_user_with_self_profile(session: AsyncSession, *, label: str) -> tuple[User, Profile]:
    user = User(
        email=f"s-{uuid4().hex[:12]}@test.local",
        hashed_password="synthetic-password-hash",
        name=f"schedule-{label}"[:20],
        gender=Gender.FEMALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
    session.add(profile)
    await session.flush()
    return user, profile


async def _create_active_version_medication(
    session: AsyncSession,
    *,
    owner: User,
    profile: Profile,
) -> tuple[Prescription, PrescriptionVersionMedication]:
    document = MedicalDocument(
        uploaded_by=owner.id,
        profile_id=profile.id,
        original_file_name="synthetic-schedule.png",
        object_key=f"synthetic/{uuid4()}.png",
        file_mime_type="image/png",
        file_size_bytes=1,
    )
    session.add(document)
    await session.flush()
    ocr_job = OcrJob(document_id=document.id)
    session.add(ocr_job)
    await session.flush()

    version_id = uuid4()
    confirmed_at = datetime(2026, 9, 9, tzinfo=UTC)
    prescription = Prescription(
        active_version_id=version_id,
        document_id=document.id,
        source_ocr_job_id=ocr_job.id,
        profile_id=profile.id,
        prescribed_date=date(2026, 9, 9),
        confirmed_at=confirmed_at,
    )
    session.add(prescription)
    await session.flush()
    version = PrescriptionVersion(
        **fingerprint_values(
            prescription.prescribed_date,
            [{"medication_name": "합성테스트약", "frequency_per_day": 1, "display_order": 1}],
        ),
        id=version_id,
        prescription_id=prescription.id,
        version_number=1,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=confirmed_at,
    )
    session.add(version)
    await session.flush()
    medication = PrescriptionVersionMedication(
        medication_count=1,
        prescription_version_id=version.id,
        medication_name="합성테스트약",
        frequency_per_day=1,
        display_order=1,
    )
    session.add(medication)
    await session.flush()
    return prescription, medication


async def _delete_committed_fixture(
    session: AsyncSession,
    *,
    owner_id: UUID,
    profile_id: UUID,
    document_id: UUID,
    ocr_job_id: UUID,
    prescription_id: UUID,
    schedule_id: UUID | None = None,
) -> None:
    if schedule_id is not None:
        await session.execute(
            delete(MedicationOccurrence).where(MedicationOccurrence.medication_schedule_id == schedule_id)
        )
        await session.execute(
            delete(MedicationScheduleTime).where(MedicationScheduleTime.medication_schedule_id == schedule_id)
        )
        await session.execute(delete(MedicationSchedule).where(MedicationSchedule.id == schedule_id))
    await session.execute(delete(Prescription).where(Prescription.id == prescription_id))
    await session.execute(delete(OcrJob).where(OcrJob.id == ocr_job_id))
    await session.execute(delete(MedicalDocument).where(MedicalDocument.id == document_id))
    await session.execute(delete(Profile).where(Profile.id == profile_id))
    await session.execute(delete(User).where(User.id == owner_id))
    await session.commit()


async def test_default_ownership_adapter_hides_schedule_and_occurrence_from_other_users(
    db_session: AsyncSession,
) -> None:
    owner, profile = await _create_user_with_self_profile(db_session, label="owner")
    intruder, _ = await _create_user_with_self_profile(db_session, label="intruder")
    _, medication = await _create_active_version_medication(
        db_session,
        owner=owner,
        profile=profile,
    )
    repository = MedicationScheduleRepository(db_session)

    schedule = await repository.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 9),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert schedule is not None
    schedule_time = (
        await repository.add_schedule_times(
            schedule=schedule,
            schedule_revision=schedule.revision,
            local_times=[time(9, 0)],
        )
    )[0]
    occurrence = await repository.create_occurrence(
        schedule=schedule,
        schedule_time=schedule_time,
        scheduled_local_date=date(2026, 9, 9),
        scheduled_at=datetime(2026, 9, 9, 0, 0, tzinfo=UTC),
        confirmation_deadline_at=datetime(2026, 9, 9, 4, 0, tzinfo=UTC),
    )

    assert await repository.get_schedule_owned(schedule_id=schedule.id, user_id=owner.id) is schedule
    assert await repository.get_schedule_owned(schedule_id=schedule.id, user_id=intruder.id) is None
    assert await repository.get_occurrence_owned(occurrence_id=occurrence.id, user_id=owner.id) is occurrence
    assert await repository.get_occurrence_owned(occurrence_id=occurrence.id, user_id=intruder.id) is None


async def test_schedule_creation_requires_the_current_prescription_version(
    db_session: AsyncSession,
) -> None:
    owner, profile = await _create_user_with_self_profile(db_session, label="version-owner")
    prescription, old_medication = await _create_active_version_medication(
        db_session,
        owner=owner,
        profile=profile,
    )
    repository = MedicationScheduleRepository(db_session)
    old_schedule = await repository.create_schedule_owned(
        prescription_version_medication_id=old_medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 9),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert old_schedule is not None

    replacement = PrescriptionVersion(
        **fingerprint_values(
            date(2026, 9, 10), [{"medication_name": "합성교체약", "frequency_per_day": 1, "display_order": 1}]
        ),
        prescription_id=prescription.id,
        version_number=2,
        prescribed_date=date(2026, 9, 10),
        confirmed_at=datetime(2026, 9, 10, tzinfo=UTC),
    )
    db_session.add(replacement)
    await db_session.flush()
    replacement_medication = PrescriptionVersionMedication(
        medication_count=1,
        prescription_version_id=replacement.id,
        medication_name="합성교체약",
        frequency_per_day=1,
        display_order=1,
    )
    db_session.add(replacement_medication)
    await db_session.flush()
    prescription.active_version_id = replacement.id
    await db_session.flush()

    assert (
        await repository.create_schedule_owned(
            prescription_version_medication_id=old_medication.id,
            user_id=owner.id,
            start_local_date=date(2026, 9, 10),
            end_mode=MedicationScheduleEndMode.OPEN_ENDED,
            end_local_date=None,
            source=MedicationScheduleSource.USER_CONFIRMED,
        )
        is None
    )
    assert await repository.get_schedule_owned(schedule_id=old_schedule.id, user_id=owner.id) is old_schedule


async def test_rolling_scheduler_is_idempotent_and_snapshots_kst_as_utc(
    db_session: AsyncSession,
) -> None:
    owner, profile = await _create_user_with_self_profile(db_session, label="rolling")
    _, medication = await _create_active_version_medication(db_session, owner=owner, profile=profile)
    repository = MedicationScheduleRepository(db_session)
    schedule = await repository.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 9),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert schedule is not None
    await repository.add_schedule_times(
        schedule=schedule,
        schedule_revision=schedule.revision,
        local_times=[time(9, 0)],
    )
    seoul = timezone(timedelta(hours=9), name="Asia/Seoul")
    scheduler = MedicationOccurrenceScheduler(repository, service_timezone=seoul)

    first = await scheduler.generate(now=datetime(2026, 9, 9, 0, 30, tzinfo=seoul))
    second = await scheduler.generate(now=datetime(2026, 9, 9, 1, 0, tzinfo=seoul))

    assert first.created_count == 14
    assert first.duplicate_count == 0
    assert second.created_count == 0
    assert second.duplicate_count == 14
    occurrences = list(
        (
            await db_session.execute(
                select(MedicationOccurrence)
                .where(MedicationOccurrence.medication_schedule_id == schedule.id)
                .order_by(MedicationOccurrence.scheduled_local_date)
            )
        )
        .scalars()
        .all()
    )
    assert len(occurrences) == 14
    assert occurrences[0].scheduled_local_date == date(2026, 9, 9)
    assert occurrences[-1].scheduled_local_date == date(2026, 9, 22)
    assert occurrences[0].scheduled_at == datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
    assert occurrences[0].confirmation_deadline_at == datetime(2026, 9, 9, 15, 0, tzinfo=UTC)


async def test_scheduler_ends_expired_schedule_without_creating_occurrences(
    db_session: AsyncSession,
) -> None:
    owner, profile = await _create_user_with_self_profile(db_session, label="ended")
    _, medication = await _create_active_version_medication(db_session, owner=owner, profile=profile)
    repository = MedicationScheduleRepository(db_session)
    schedule = await repository.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 1),
        end_mode=MedicationScheduleEndMode.DATE,
        end_local_date=date(2026, 9, 8),
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert schedule is not None
    await repository.add_schedule_times(
        schedule=schedule,
        schedule_revision=schedule.revision,
        local_times=[time(9, 0)],
    )
    seoul = timezone(timedelta(hours=9), name="Asia/Seoul")

    result = await MedicationOccurrenceScheduler(repository, service_timezone=seoul).generate(
        now=datetime(2026, 9, 9, tzinfo=seoul)
    )

    assert result.created_count == 0
    assert result.ended_schedule_count == 1
    assert schedule.status == MedicationScheduleStatus.ENDED


async def test_concurrent_scheduler_runs_create_each_occurrence_once() -> None:
    async with AsyncSession(test_engine, expire_on_commit=False, autoflush=False) as setup_session:
        owner, profile = await _create_user_with_self_profile(setup_session, label="concurrent")
        prescription, medication = await _create_active_version_medication(
            setup_session,
            owner=owner,
            profile=profile,
        )
        repository = MedicationScheduleRepository(setup_session)
        schedule = await repository.create_schedule_owned(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            start_local_date=date(2026, 9, 9),
            end_mode=MedicationScheduleEndMode.OPEN_ENDED,
            end_local_date=None,
            source=MedicationScheduleSource.USER_CONFIRMED,
        )
        assert schedule is not None
        await repository.add_schedule_times(
            schedule=schedule,
            schedule_revision=schedule.revision,
            local_times=[time(9, 0)],
        )
        await setup_session.commit()
        schedule_id = schedule.id
        cleanup_ids = {
            "owner_id": owner.id,
            "profile_id": profile.id,
            "document_id": prescription.document_id,
            "ocr_job_id": prescription.source_ocr_job_id,
            "prescription_id": prescription.id,
            "schedule_id": schedule.id,
        }

    seoul = timezone(timedelta(hours=9), name="Asia/Seoul")

    async def run_once():
        async with AsyncSession(test_engine, expire_on_commit=False, autoflush=False) as session:
            result = await MedicationOccurrenceScheduler(
                MedicationScheduleRepository(session),
                service_timezone=seoul,
            ).generate(now=datetime(2026, 9, 9, tzinfo=seoul))
            await session.commit()
            return result

    first, second = await asyncio.gather(run_once(), run_once())

    assert sorted((first.created_count, second.created_count)) == [0, 14]
    assert sorted((first.duplicate_count, second.duplicate_count)) == [0, 14]
    async with AsyncSession(test_engine, expire_on_commit=False, autoflush=False) as verify_session:
        occurrence_count = await verify_session.scalar(
            select(func.count(MedicationOccurrence.id)).where(
                MedicationOccurrence.medication_schedule_id == schedule_id
            )
        )
        assert occurrence_count == 14
        await _delete_committed_fixture(verify_session, **cleanup_ids)


async def test_version_invalidation_cancels_only_future_pending_occurrences(
    db_session: AsyncSession,
) -> None:
    owner, profile = await _create_user_with_self_profile(db_session, label="cancel")
    prescription, medication = await _create_active_version_medication(db_session, owner=owner, profile=profile)
    repository = MedicationScheduleRepository(db_session)
    schedule = await repository.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 9),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert schedule is not None
    schedule_time = (
        await repository.add_schedule_times(
            schedule=schedule,
            schedule_revision=schedule.revision,
            local_times=[time(9, 0)],
        )
    )[0]
    effective_at = datetime(2026, 9, 10, tzinfo=UTC)

    async def create(local_date: date, scheduled_at: datetime, status: MedicationOccurrenceStatus):
        return await repository.create_occurrence(
            schedule=schedule,
            schedule_time=schedule_time,
            scheduled_local_date=local_date,
            scheduled_at=scheduled_at,
            confirmation_deadline_at=scheduled_at + timedelta(hours=4),
            status=status,
        )

    past_pending = await create(date(2026, 9, 9), effective_at - timedelta(hours=1), MedicationOccurrenceStatus.PENDING)
    future_pending = await create(date(2026, 9, 10), effective_at, MedicationOccurrenceStatus.PENDING)
    future_closed = await create(date(2026, 9, 11), effective_at + timedelta(days=1), MedicationOccurrenceStatus.CLOSED)
    future_cancelled = await create(
        date(2026, 9, 12),
        effective_at + timedelta(days=2),
        MedicationOccurrenceStatus.CANCELLED,
    )

    cancelled_ids = await repository.cancel_future_for_prescription_version(
        prescription_version_id=prescription.active_version_id,
        effective_at=effective_at,
    )

    assert cancelled_ids == (future_pending.id,)
    assert past_pending.status == MedicationOccurrenceStatus.PENDING
    assert future_pending.status == MedicationOccurrenceStatus.CANCELLED
    assert future_closed.status == MedicationOccurrenceStatus.CLOSED
    assert future_cancelled.status == MedicationOccurrenceStatus.CANCELLED
    assert schedule.status == MedicationScheduleStatus.ACTIVE


async def test_schedule_creation_waits_for_version_transition_and_rejects_old_version() -> None:
    async with AsyncSession(test_engine, expire_on_commit=False, autoflush=False) as setup_session:
        owner, profile = await _create_user_with_self_profile(setup_session, label="race")
        prescription, old_medication = await _create_active_version_medication(
            setup_session,
            owner=owner,
            profile=profile,
        )
        replacement = PrescriptionVersion(
            **fingerprint_values(
                date(2026, 9, 10), [{"medication_name": "합성경합교체약", "frequency_per_day": 1, "display_order": 1}]
            ),
            prescription_id=prescription.id,
            version_number=2,
            prescribed_date=date(2026, 9, 10),
            confirmed_at=datetime(2026, 9, 10, tzinfo=UTC),
        )
        setup_session.add(replacement)
        await setup_session.flush()
        setup_session.add(
            PrescriptionVersionMedication(
                medication_count=1,
                prescription_version_id=replacement.id,
                medication_name="합성경합교체약",
                frequency_per_day=1,
                display_order=1,
            )
        )
        await setup_session.commit()
        owner_id = owner.id
        prescription_id = prescription.id
        old_medication_id = old_medication.id
        replacement_id = replacement.id
        cleanup_ids = {
            "owner_id": owner.id,
            "profile_id": profile.id,
            "document_id": prescription.document_id,
            "ocr_job_id": prescription.source_ocr_job_id,
            "prescription_id": prescription.id,
        }

    async with (
        AsyncSession(test_engine, expire_on_commit=False, autoflush=False) as version_session,
        AsyncSession(test_engine, expire_on_commit=False, autoflush=False) as schedule_session,
    ):
        locked_prescription = await version_session.scalar(
            select(Prescription).where(Prescription.id == prescription_id).with_for_update()
        )
        assert locked_prescription is not None
        create_task = asyncio.create_task(
            MedicationScheduleRepository(schedule_session).create_schedule_owned(
                prescription_version_medication_id=old_medication_id,
                user_id=owner_id,
                start_local_date=date(2026, 9, 10),
                end_mode=MedicationScheduleEndMode.OPEN_ENDED,
                end_local_date=None,
                source=MedicationScheduleSource.USER_CONFIRMED,
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(create_task), timeout=0.1)

        locked_prescription.active_version_id = replacement_id
        await version_session.commit()
        assert await asyncio.wait_for(create_task, timeout=1) is None
        await schedule_session.rollback()

    async with AsyncSession(test_engine, expire_on_commit=False, autoflush=False) as verify_session:
        old_schedule = await verify_session.scalar(
            select(MedicationSchedule).where(MedicationSchedule.prescription_version_medication_id == old_medication_id)
        )
        assert old_schedule is None
        await _delete_committed_fixture(verify_session, **cleanup_ids)
