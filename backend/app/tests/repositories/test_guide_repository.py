from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.guides import Guide, GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.guide_repository import GuideRepository
from app.tests.conftest import test_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # conftest.isolate_database와 동일한 savepoint 격리 방식을 사용해,
    # 리포지토리 메서드를 HTTP 계층 없이 직접 테스트하면서도 다른 테스트에 영향을 주지 않습니다.
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


async def _create_confirmed_prescription(session: AsyncSession, *, user: User) -> Prescription:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="prescription.jpg",
        object_key="prescription.jpg",
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
    session.add(prescription)
    await session.flush()

    session.add(Medication(prescription_id=prescription.id, medication_name="타이레놀", display_order=1))
    await session.flush()

    return prescription


async def test_get_prescription_owned_rejects_other_users_prescription(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="owner@example.com")
    intruder = await _create_user(db_session, email="intruder@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    repo = GuideRepository(db_session)

    owned = await repo.get_prescription_owned(prescription_id=prescription.id, user_id=owner.id)
    assert owned is not None

    stolen = await repo.get_prescription_owned(prescription_id=prescription.id, user_id=intruder.id)
    assert stolen is None


async def test_get_prescription_owned_orders_medications_by_display_order(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="ordered-medications@example.com")
    # _create_confirmed_prescription이 display_order=1 약물을 먼저 저장하므로,
    # 삽입 순서와 display_order 순서가 어긋나도록 3번을 2번보다 먼저 저장합니다.
    # 정렬 없이 삽입(행 생성) 순서로만 조회하면 [1, 3, 2]가 나오고,
    # display_order 기준으로 정렬해야만 [1, 2, 3]이 나옵니다.
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    db_session.add(
        Medication(
            prescription_id=prescription.id,
            medication_name="세번째 약",
            display_order=3,
        )
    )
    await db_session.flush()
    db_session.add(
        Medication(
            prescription_id=prescription.id,
            medication_name="두번째 약",
            display_order=2,
        )
    )
    await db_session.flush()

    repo = GuideRepository(db_session)
    loaded = await repo.get_prescription_owned(prescription_id=prescription.id, user_id=owner.id)

    assert loaded is not None
    assert [medication.display_order for medication in loaded.medications] == [1, 2, 3]


async def test_get_owned_guide_rejects_other_users_guide(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="owner2@example.com")
    intruder = await _create_user(db_session, email="intruder2@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    repo = GuideRepository(db_session)
    guide = await repo.create(prescription=prescription)

    owned = await repo.get_owned(guide_id=guide.id, user_id=owner.id)
    assert owned is not None

    stolen = await repo.get_owned(guide_id=guide.id, user_id=intruder.id)
    assert stolen is None


async def test_mark_failed_persists_after_writer_session_closes_and_new_session_reloads() -> None:
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    synthetic_suffix = uuid4().hex[:12]

    async with session_factory() as writer_session:
        user = await _create_user(writer_session, email=f"guide-failure-{synthetic_suffix}@example.com")
        prescription = await _create_confirmed_prescription(writer_session, user=user)
        guide = await GuideRepository(writer_session).create(prescription=prescription)

        await GuideRepository(writer_session).mark_failed(
            guide,
            error_code="OPENAI_API_ERROR",
            error_message="고정된 안전 문구",
            completed_at=datetime.now(UTC),
        )

        # 실제 요청 흐름에서는 commit 뒤 ApiError가 전파되어 dependency가 rollback을 호출합니다.
        # 실패 상태가 commit 경계를 넘었는지 검증하기 위해 writer session은 여기서 완전히 닫습니다.
        await writer_session.rollback()
        guide_id = guide.id
        prescription_id = prescription.id
        ocr_job_id = prescription.source_ocr_job_id
        document_id = prescription.document_id
        user_id = user.id

    try:
        async with session_factory() as verification_session:
            reloaded = await GuideRepository(verification_session).get_owned(
                guide_id=guide_id,
                user_id=user_id,
            )
            assert reloaded is not None
            assert reloaded.generation_status == GuideGenerationStatus.FAILED
            assert reloaded.error_code == "OPENAI_API_ERROR"
            assert reloaded.error_message == "고정된 안전 문구"
            assert reloaded.completed_at is not None
            assert (reloaded.content, reloaded.model_name, reloaded.prompt_version) == (None, None, None)
    finally:
        async with session_factory() as cleanup_session:
            await cleanup_session.execute(delete(Guide).where(Guide.id == guide_id))
            await cleanup_session.execute(delete(Medication).where(Medication.prescription_id == prescription_id))
            await cleanup_session.execute(delete(Prescription).where(Prescription.id == prescription_id))
            await cleanup_session.execute(delete(OcrJob).where(OcrJob.id == ocr_job_id))
            await cleanup_session.execute(delete(MedicalDocument).where(MedicalDocument.id == document_id))
            await cleanup_session.execute(delete(Profile).where(Profile.user_id == user_id))
            await cleanup_session.execute(delete(User).where(User.id == user_id))
            await cleanup_session.commit()
