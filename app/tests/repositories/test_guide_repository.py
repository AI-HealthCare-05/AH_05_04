from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guides import GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
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
    return user


async def _create_confirmed_prescription(session: AsyncSession, *, user: User) -> Prescription:
    document = MedicalDocument(
        user_id=user.id,
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
    prescription = await _create_confirmed_prescription(db_session, user=owner)
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
    assert [medication.display_order for medication in loaded.medications] == [1, 2]


async def test_get_owned_guide_rejects_other_users_guide(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="owner2@example.com")
    intruder = await _create_user(db_session, email="intruder2@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    repo = GuideRepository(db_session)
    guide = await repo.create(prescription_id=prescription.id)

    owned = await repo.get_owned(guide_id=guide.id, user_id=owner.id)
    assert owned is not None

    stolen = await repo.get_owned(guide_id=guide.id, user_id=intruder.id)
    assert stolen is None


async def test_mark_failed_persists_after_subsequent_rollback(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email="failure@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=user)

    repo = GuideRepository(db_session)
    guide = await repo.create(prescription_id=prescription.id)

    await repo.mark_failed(
        guide,
        error_code="OPENAI_API_ERROR",
        error_message="고정된 안전 문구",
        completed_at=datetime.now(UTC),
    )

    # 실제 요청 흐름에서는 이 시점 이후 서비스가 ApiError를 다시 발생시키고,
    # get_db_session의 예외 처리가 session.rollback()을 호출합니다. 그 상황을 재현합니다.
    await db_session.rollback()

    reloaded = await repo.get_owned(guide_id=guide.id, user_id=user.id)
    assert reloaded is not None
    assert reloaded.generation_status == GuideGenerationStatus.FAILED
    assert reloaded.error_message == "고정된 안전 문구"
