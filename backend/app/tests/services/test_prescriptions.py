from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.dtos.prescriptions import CorrectPrescriptionRequest, PrescriptionMedicationCorrectionRequest
from app.models.async_jobs import (
    AiJob,
    AiJobAttempt,
    AiJobAttemptStatus,
    AiJobStatus,
    AiJobType,
    OutboxEvent,
    OutboxEventStatus,
)
from app.models.medical_documents import MedicalDocument
from app.models.medication_schedules import (
    MedicationOccurrenceStatus,
    MedicationSchedule,
    MedicationScheduleEndMode,
    MedicationScheduleSource,
)
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import MedicationCandidateSearch, MedicationCandidateSearchStatus
from app.models.users import Gender, User
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.medication_schedule_repository import MedicationScheduleRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.medication_occurrences import PrescriptionVersionMedicationInvalidationService
from app.services.prescriptions import PrescriptionService
from app.tests.conftest import test_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


def _service(session: AsyncSession) -> PrescriptionService:
    return PrescriptionService(
        document_repository=MedicalDocumentRepository(session),
        ocr_repository=OcrRepository(session),
        prescription_repository=PrescriptionRepository(session),
        schedule_invalidation=PrescriptionVersionMedicationInvalidationService(MedicationScheduleRepository(session)),
    )


async def _create_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email,
        hashed_password="hashed-password",
        name="테스트 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
    session.add(profile)
    await session.flush()
    return user


async def _create_confirmed_prescription(session: AsyncSession, *, user: User) -> Prescription:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="prescription.jpg",
        object_key=f"{uuid4()}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    session.add(document)
    await session.flush()

    ocr_job = OcrJob(document_id=document.id)
    session.add(ocr_job)
    await session.flush()

    return await PrescriptionRepository(session).create_with_medications(
        document=document,
        source_ocr_job=ocr_job,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
        medications=[{"medication_name": "타이레놀", "display_order": 1}],
    )


async def test_get_latest_prescription_returns_owned_prescription(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="latest-prescription-owner@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    result = await service.get_latest_prescription(user=owner)

    assert result.prescription_id == prescription.id
    assert len(result.medications) == 1


async def test_get_latest_prescription_raises_not_found_without_any_prescription(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="latest-prescription-none@example.com")

    with pytest.raises(ApiError) as exc_info:
        await service.get_latest_prescription(user=owner)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PRESCRIPTION_NOT_FOUND"


async def test_get_latest_prescription_rejects_other_users_prescription(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="latest-prescription-owner2@example.com")
    intruder = await _create_user(db_session, email="latest-prescription-intruder@example.com")
    await _create_confirmed_prescription(db_session, user=owner)

    with pytest.raises(ApiError) as exc_info:
        await service.get_latest_prescription(user=intruder)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PRESCRIPTION_NOT_FOUND"


async def test_correction_creates_new_immutable_version_and_switches_active_read(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="correction-owner@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    base_version_id = prescription.active_version_id
    assert base_version_id is not None

    result = await service.correct_prescription(
        user=owner,
        prescription_id=prescription.id,
        request=CorrectPrescriptionRequest(
            base_version_id=base_version_id,
            expected_revision=1,
            prescribed_date=date(2026, 9, 8),
            medications=[
                PrescriptionMedicationCorrectionRequest(
                    medication_name="  정정된 합성약  ",
                    strength_text="  5mg  ",
                    dose_value=Decimal("0.5"),
                    dose_unit="  정  ",
                    frequency_per_day=2,
                    timing_text="  식후  ",
                    duration_days=5,
                    display_order=1,
                )
            ],
        ),
    )

    assert result.revision == 2
    assert result.current is True
    assert result.prescription_version_id != base_version_id
    assert result.medications[0].medication_name == "정정된 합성약"
    assert result.medications[0].strength_text == "5mg"
    assert result.medications[0].dose_unit == "정"
    assert result.medications[0].timing_text == "식후"
    old_medications = await PrescriptionRepository(db_session).get_version_medications(
        prescription_version_id=base_version_id
    )
    assert old_medications[0].medication_name == "타이레놀"


async def test_correction_rejects_stale_base_version_without_creating_version(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="correction-conflict@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    with pytest.raises(ApiError) as exc_info:
        await service.correct_prescription(
            user=owner,
            prescription_id=prescription.id,
            request=CorrectPrescriptionRequest(
                base_version_id=uuid4(),
                expected_revision=1,
                prescribed_date=date.today(),
                medications=[
                    PrescriptionMedicationCorrectionRequest(
                        medication_name="정정 시도 약",
                        display_order=1,
                    )
                ],
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PRESCRIPTION_VERSION_CONFLICT"


async def test_correction_invalidates_previous_version_jobs_outbox_and_candidate_search(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="correction-invalidation@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    base_version_id = prescription.active_version_id
    assert base_version_id is not None
    medication = await db_session.scalar(
        select(PrescriptionVersionMedication).where(
            PrescriptionVersionMedication.prescription_version_id == base_version_id
        )
    )
    assert medication is not None

    processing_job = AiJob(
        user_id=owner.id,
        job_type=AiJobType.GUIDE,
        status=AiJobStatus.PROCESSING,
        prescription_version_id=base_version_id,
        max_attempts=3,
        attempt_count=1,
        lease_token="synthetic-lease",
        lease_expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    pending_job = AiJob(
        user_id=owner.id,
        job_type=AiJobType.CHAT,
        status=AiJobStatus.PENDING,
        prescription_version_id=base_version_id,
        max_attempts=2,
    )
    retry_job = AiJob(
        user_id=owner.id,
        job_type=AiJobType.GUIDE,
        status=AiJobStatus.RETRY_WAIT,
        prescription_version_id=base_version_id,
        max_attempts=3,
        attempt_count=1,
    )
    completed_job = AiJob(
        user_id=owner.id,
        job_type=AiJobType.GUIDE,
        status=AiJobStatus.COMPLETED,
        prescription_version_id=base_version_id,
        max_attempts=3,
        completed_at=datetime.now(UTC),
    )
    db_session.add_all([processing_job, pending_job, retry_job, completed_job])
    await db_session.flush()
    attempt = AiJobAttempt(
        ai_job_id=processing_job.id,
        attempt_no=1,
        attempt_status=AiJobAttemptStatus.PROCESSING,
    )
    claimed_event = OutboxEvent(
        job_id=processing_job.id,
        attempt=1,
        status=OutboxEventStatus.CLAIMED,
        claim_token="synthetic-claim",
        claim_expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    pending_event = OutboxEvent(
        job_id=pending_job.id,
        attempt=1,
        status=OutboxEventStatus.PENDING,
    )
    published_event = OutboxEvent(
        job_id=completed_job.id,
        attempt=1,
        status=OutboxEventStatus.PUBLISHED,
        published_at=datetime.now(UTC),
    )
    completed_job_pending_event = OutboxEvent(
        job_id=completed_job.id,
        attempt=2,
        status=OutboxEventStatus.PENDING,
    )
    search = MedicationCandidateSearch(
        prescription_version_medication_id=medication.id,
        medication_name_snapshot=medication.medication_name,
        strength_text_snapshot=medication.strength_text,
        query_digest="a" * 64,
        status=MedicationCandidateSearchStatus.RUNNING,
        candidate_count=0,
        displayed_candidate_count=0,
    )
    finished_search = MedicationCandidateSearch(
        prescription_version_medication_id=medication.id,
        medication_name_snapshot=medication.medication_name,
        strength_text_snapshot=medication.strength_text,
        query_digest="b" * 64,
        status=MedicationCandidateSearchStatus.NO_CANDIDATE,
        candidate_count=0,
        displayed_candidate_count=0,
        finalized_at=datetime.now(UTC),
    )
    db_session.add_all(
        [
            attempt,
            claimed_event,
            pending_event,
            published_event,
            completed_job_pending_event,
            search,
            finished_search,
        ]
    )
    await db_session.flush()
    processing_job.expected_event_id = claimed_event.event_id
    pending_job.expected_event_id = pending_event.event_id
    await db_session.flush()

    await service.correct_prescription(
        user=owner,
        prescription_id=prescription.id,
        request=CorrectPrescriptionRequest(
            base_version_id=base_version_id,
            expected_revision=1,
            prescribed_date=date.today(),
            medications=[PrescriptionMedicationCorrectionRequest(medication_name="정정약", display_order=1)],
        ),
    )

    assert processing_job.status == AiJobStatus.STALE
    assert pending_job.status == AiJobStatus.STALE
    assert retry_job.status == AiJobStatus.STALE
    assert processing_job.completed_at is not None
    assert processing_job.last_consumed_event_id == claimed_event.event_id
    assert processing_job.lease_token is None
    assert attempt.attempt_status == AiJobAttemptStatus.BLOCKED
    assert attempt.completed_at is not None
    assert claimed_event.status == OutboxEventStatus.CANCELLED
    assert claimed_event.claim_token is None
    assert pending_event.status == OutboxEventStatus.CANCELLED
    assert completed_job.status == AiJobStatus.COMPLETED
    assert published_event.status == OutboxEventStatus.PUBLISHED
    assert completed_job_pending_event.status == OutboxEventStatus.PENDING
    assert search.status == MedicationCandidateSearchStatus.INVALIDATED_INPUT_CHANGED
    assert search.invalidated_at is not None
    assert finished_search.status == MedicationCandidateSearchStatus.NO_CANDIDATE


async def test_correction_cancels_only_future_pending_occurrences_without_copying_schedule(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="correction-schedule@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    base_version_id = prescription.active_version_id
    assert base_version_id is not None
    medication = await db_session.scalar(
        select(PrescriptionVersionMedication).where(
            PrescriptionVersionMedication.prescription_version_id == base_version_id
        )
    )
    assert medication is not None
    schedule_repository = MedicationScheduleRepository(db_session)
    schedule = await schedule_repository.create_schedule_owned(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        start_local_date=date(2026, 9, 9),
        end_mode=MedicationScheduleEndMode.OPEN_ENDED,
        end_local_date=None,
        source=MedicationScheduleSource.USER_CONFIRMED,
    )
    assert schedule is not None
    schedule_time = (
        await schedule_repository.add_schedule_times(
            schedule=schedule,
            schedule_revision=schedule.revision,
            local_times=[time(9, 0)],
        )
    )[0]
    past_pending = await schedule_repository.create_occurrence(
        schedule=schedule,
        schedule_time=schedule_time,
        scheduled_local_date=date(2020, 1, 1),
        scheduled_at=datetime(2020, 1, 1, tzinfo=UTC),
        confirmation_deadline_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=4),
    )
    future_pending = await schedule_repository.create_occurrence(
        schedule=schedule,
        schedule_time=schedule_time,
        scheduled_local_date=date(2099, 1, 1),
        scheduled_at=datetime(2099, 1, 1, tzinfo=UTC),
        confirmation_deadline_at=datetime(2099, 1, 1, tzinfo=UTC) + timedelta(hours=4),
    )
    future_closed = await schedule_repository.create_occurrence(
        schedule=schedule,
        schedule_time=schedule_time,
        scheduled_local_date=date(2099, 1, 2),
        scheduled_at=datetime(2099, 1, 2, tzinfo=UTC),
        confirmation_deadline_at=datetime(2099, 1, 2, tzinfo=UTC) + timedelta(hours=4),
        status=MedicationOccurrenceStatus.CLOSED,
    )

    result = await service.correct_prescription(
        user=owner,
        prescription_id=prescription.id,
        request=CorrectPrescriptionRequest(
            base_version_id=base_version_id,
            expected_revision=1,
            prescribed_date=date(2026, 9, 9),
            medications=[PrescriptionMedicationCorrectionRequest(medication_name="새 합성약", display_order=1)],
        ),
    )

    assert past_pending.status == MedicationOccurrenceStatus.PENDING
    assert future_pending.status == MedicationOccurrenceStatus.CANCELLED
    assert future_closed.status == MedicationOccurrenceStatus.CLOSED
    assert schedule.status.value == "ACTIVE"
    new_schedule = await db_session.scalar(
        select(MedicationSchedule)
        .join(
            PrescriptionVersionMedication,
            PrescriptionVersionMedication.id == MedicationSchedule.prescription_version_medication_id,
        )
        .where(PrescriptionVersionMedication.prescription_version_id == result.prescription_version_id)
    )
    assert new_schedule is None


@pytest.mark.parametrize("corruption", ["content", "medication_missing"])
async def test_corrupted_prescription_is_not_returned(db_session, corruption):
    from sqlalchemy import delete, update

    owner = await _create_user(db_session, email=f"seal-{corruption}@test.local")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    version_id = prescription.active_version_id
    if corruption == "content":
        await db_session.execute(
            update(PrescriptionVersionMedication)
            .where(PrescriptionVersionMedication.prescription_version_id == version_id)
            .values(medication_name="Synthetic changed")
        )
    else:
        await db_session.execute(
            delete(PrescriptionVersionMedication).where(
                PrescriptionVersionMedication.prescription_version_id == version_id
            )
        )
    db_session.expire_all()
    await db_session.refresh(owner)
    with pytest.raises(ApiError) as error:
        await _service(db_session).get_latest_prescription(user=owner)
    assert error.value.status_code == 409
    assert error.value.code == "PRESCRIPTION_VERSION_UNAVAILABLE"
