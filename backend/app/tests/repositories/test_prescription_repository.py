from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import (
    Medication,
    Prescription,
    PrescriptionVersion,
    PrescriptionVersionMedication,
)
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.prescription_repository import PrescriptionRepository
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


async def _create_confirmed_prescription(
    session: AsyncSession, *, user: User, created_at: datetime | None = None
) -> Prescription:
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

    prescription = await PrescriptionRepository(session).create_with_medications(
        document=document,
        source_ocr_job=ocr_job,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
        medications=[{"medication_name": "타이레놀", "display_order": 1}],
    )
    if created_at is not None:
        # PostgreSQL의 now()는 트랜잭션 시작 시각을 반환하므로, 같은 savepoint 트랜잭션 안에서
        # server_default만으로 생성한 두 row는 created_at이 동일할 수 있습니다. "가장 최근" 정렬을
        # 결정적으로 검증하기 위해 필요할 때만 명시적으로 다른 값을 지정합니다.
        prescription.created_at = created_at
        await session.flush()
    return prescription


async def test_get_latest_owned_returns_none_without_any_prescription(db_session: AsyncSession) -> None:
    repo = PrescriptionRepository(db_session)
    owner = await _create_user(db_session, email="latest-none@example.com")

    result = await repo.get_latest_owned(user_id=owner.id)

    assert result is None


async def test_get_latest_owned_returns_most_recently_created_prescription(db_session: AsyncSession) -> None:
    repo = PrescriptionRepository(db_session)
    owner = await _create_user(db_session, email="latest-two@example.com")
    first = await _create_confirmed_prescription(db_session, user=owner, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    second = await _create_confirmed_prescription(db_session, user=owner, created_at=datetime(2026, 1, 2, tzinfo=UTC))

    result = await repo.get_latest_owned(user_id=owner.id)

    assert result is not None
    assert result.id == second.id
    assert result.id != first.id


async def test_get_latest_owned_rejects_other_users_prescriptions(db_session: AsyncSession) -> None:
    repo = PrescriptionRepository(db_session)
    owner = await _create_user(db_session, email="latest-owner@example.com")
    intruder = await _create_user(db_session, email="latest-intruder@example.com")
    await _create_confirmed_prescription(db_session, user=owner)

    result = await repo.get_latest_owned(user_id=intruder.id)

    assert result is None


async def test_create_with_medications_writes_only_version_one_snapshot(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="version-write@example.com")
    profile = await db_session.scalar(
        select(Profile).where(Profile.user_id == owner.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = MedicalDocument(
        uploaded_by=owner.id,
        profile_id=profile.id,
        original_file_name="version-write.jpg",
        object_key=f"{uuid4()}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    db_session.add(document)
    await db_session.flush()
    ocr_job = OcrJob(document_id=document.id)
    db_session.add(ocr_job)
    await db_session.flush()

    confirmed_at = datetime.now(UTC)
    prescription = await PrescriptionRepository(db_session).create_with_medications(
        document=document,
        source_ocr_job=ocr_job,
        prescribed_date=date(2026, 9, 8),
        confirmed_at=confirmed_at,
        medications=[
            {
                "medication_name": "합성검증정",
                "strength_text": "10mg",
                "dose_value": 1,
                "dose_unit": "정",
                "frequency_per_day": 2,
                "timing_text": "식후",
                "duration_days": 3,
                "display_order": 1,
            }
        ],
    )

    version = await db_session.scalar(
        select(PrescriptionVersion).where(PrescriptionVersion.prescription_id == prescription.id)
    )
    assert version is not None
    assert prescription.active_version_id == version.id
    assert version.version_number == 1
    assert version.prescribed_date == prescription.prescribed_date
    assert version.confirmed_at == confirmed_at

    legacy_medication = await db_session.scalar(select(Medication).where(Medication.prescription_id == prescription.id))
    snapshot_medication = await db_session.scalar(
        select(PrescriptionVersionMedication).where(PrescriptionVersionMedication.prescription_version_id == version.id)
    )
    assert legacy_medication is None
    assert snapshot_medication is not None
    assert snapshot_medication.medication_name == "합성검증정"
    assert snapshot_medication.strength_text == "10mg"
    assert snapshot_medication.dose_value == 1
    assert snapshot_medication.dose_unit == "정"
    assert snapshot_medication.frequency_per_day == 2
    assert snapshot_medication.timing_text == "식후"
    assert snapshot_medication.duration_days == 3
    assert snapshot_medication.display_order == 1


async def test_empty_or_invalid_medication_slots_leave_version_and_pointer_unchanged(db_session):
    import pytest
    from sqlalchemy import func

    owner = await _create_user(db_session, email="synthetic-slots398@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    original_id = prescription.active_version_id
    repository = PrescriptionRepository(db_session)
    for medications in (
        [],
        [{"medication_name": "Synthetic", "display_order": 2}],
        [{"medication_name": "Synthetic", "display_order": True}],
        [{"medication_name": "Synthetic", "display_order": 1}] * 2,
    ):
        with pytest.raises(ValueError):
            await repository.create_version(
                prescription=prescription,
                prescribed_date=date.today(),
                confirmed_at=datetime.now(UTC),
                medications=medications,
            )
    assert prescription.active_version_id == original_id
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PrescriptionVersion)
            .where(PrescriptionVersion.prescription_id == prescription.id)
        )
        == 1
    )


async def test_failed_medication_insert_does_not_leave_new_version_even_if_caller_commits(db_session):
    import pytest
    from sqlalchemy import func
    from sqlalchemy.exc import IntegrityError

    owner = await _create_user(db_session, email="synthetic-rollback398@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    prescription_id, original_id = prescription.id, prescription.active_version_id
    with pytest.raises(IntegrityError):
        await PrescriptionRepository(db_session).create_version(
            prescription=prescription,
            prescribed_date=date.today(),
            confirmed_at=datetime.now(UTC),
            medications=[{"medication_name": "Synthetic", "display_order": 1, "dose_value": -1}],
        )
    await db_session.commit()
    assert (
        await db_session.scalar(select(Prescription.active_version_id).where(Prescription.id == prescription_id))
        == original_id
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PrescriptionVersion)
            .where(PrescriptionVersion.prescription_id == prescription_id)
        )
        == 1
    )
