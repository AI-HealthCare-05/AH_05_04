from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
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

    prescription = Prescription(
        document_id=document.id,
        source_ocr_job_id=ocr_job.id,
        profile_id=profile.id,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
    )
    if created_at is not None:
        # PostgreSQL의 now()는 트랜잭션 시작 시각을 반환하므로, 같은 savepoint 트랜잭션 안에서
        # server_default만으로 생성한 두 row는 created_at이 동일할 수 있습니다. "가장 최근" 정렬을
        # 결정적으로 검증하기 위해 필요할 때만 명시적으로 다른 값을 지정합니다.
        prescription.created_at = created_at
    session.add(prescription)
    await session.flush()

    session.add(Medication(prescription_id=prescription.id, medication_name="타이레놀", display_order=1))
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
