from datetime import UTC, date, datetime, time
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_documents import MedicalDocument
from app.models.medication_schedules import MedicationScheduleEndMode, MedicationScheduleSource
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.medication_schedule_repository import MedicationScheduleRepository


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
        id=version_id,
        prescription_id=prescription.id,
        version_number=1,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=confirmed_at,
    )
    session.add(version)
    await session.flush()
    medication = PrescriptionVersionMedication(
        prescription_version_id=version.id,
        medication_name="합성테스트약",
        frequency_per_day=1,
        display_order=1,
    )
    session.add(medication)
    await session.flush()
    return prescription, medication


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
        prescription_id=prescription.id,
        version_number=2,
        prescribed_date=date(2026, 9, 10),
        confirmed_at=datetime(2026, 9, 10, tzinfo=UTC),
    )
    db_session.add(replacement)
    await db_session.flush()
    replacement_medication = PrescriptionVersionMedication(
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
