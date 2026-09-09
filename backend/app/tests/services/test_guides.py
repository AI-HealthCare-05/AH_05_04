from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.guide_repository import GuideRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.services.guide_ai.client import ProviderGuideResponse
from app.services.guide_ai.generator import GuideGenerator
from app.services.guides import GuideService
from app.tests.conftest import test_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # test_guide_repository.py와 동일한 savepoint 격리 방식으로,
    # HTTP 계층 없이 service를 직접 테스트하면서도 다른 테스트에 영향을 주지 않습니다.
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


class _UnusedGuideProvider:
    """재접속 복구 조회는 OpenAI를 호출하지 않아야 하므로, 호출되면 즉시 실패시킵니다."""

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderGuideResponse:
        raise AssertionError("재접속 복구 조회에서 Guide Provider가 호출되면 안 됩니다.")


def _service(session: AsyncSession) -> GuideService:
    generator = GuideGenerator(provider=_UnusedGuideProvider(), model="test-model", timeout_seconds=1.0)
    return GuideService(GuideRepository(session), generator)


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

    version_id = uuid4()
    prescription = Prescription(
        active_version_id=version_id,
        document_id=document.id,
        source_ocr_job_id=ocr_job.id,
        profile_id=profile.id,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
    )
    session.add(prescription)
    await session.flush()
    version = PrescriptionVersion(
        id=version_id,
        prescription_id=prescription.id,
        version_number=1,
        prescribed_date=prescription.prescribed_date,
        confirmed_at=prescription.confirmed_at,
    )
    session.add(version)
    await session.flush()
    session.add(Medication(prescription_id=prescription.id, medication_name="타이레놀", display_order=1))
    session.add(
        PrescriptionVersionMedication(
            prescription_version_id=version_id,
            medication_name="타이레놀",
            display_order=1,
        )
    )
    await session.flush()

    return prescription


async def test_get_latest_guide_for_prescription_returns_completed_guide(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="guide-owner@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    guide = await GuideRepository(db_session).create(prescription=prescription)
    await GuideRepository(db_session).mark_completed(
        guide,
        content="복약 가이드 본문",
        model_name="test-model",
        prompt_version="guide-prompt-v1",
        completed_at=datetime.now(UTC),
    )

    result = await service.get_latest_guide_for_prescription(user=owner, prescription_id=prescription.id)

    assert result.guide_id == guide.id
    assert result.prescription_id == prescription.id
    assert result.content == "복약 가이드 본문"


async def test_get_latest_guide_for_prescription_raises_not_found_when_no_guide_exists(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="guide-none@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)

    with pytest.raises(ApiError) as exc_info:
        await service.get_latest_guide_for_prescription(user=owner, prescription_id=prescription.id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "GUIDE_NOT_FOUND"


async def test_get_latest_guide_for_prescription_rejects_other_users_prescription(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="guide-owner2@example.com")
    intruder = await _create_user(db_session, email="guide-intruder@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    guide = await GuideRepository(db_session).create(prescription=prescription)
    await GuideRepository(db_session).mark_completed(
        guide,
        content="복약 가이드 본문",
        model_name="test-model",
        prompt_version="guide-prompt-v1",
        completed_at=datetime.now(UTC),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.get_latest_guide_for_prescription(user=intruder, prescription_id=prescription.id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "GUIDE_NOT_FOUND"


async def test_previous_version_guide_is_not_exposed_as_current(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="guide-stale-version@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    guide = await GuideRepository(db_session).create(prescription=prescription)
    await GuideRepository(db_session).mark_completed(
        guide,
        content="이전 버전 합성 가이드",
        model_name="test-model",
        prompt_version="guide-prompt-v1",
        completed_at=datetime.now(UTC),
    )
    await PrescriptionRepository(db_session).create_version(
        prescription=prescription,
        prescribed_date=date.today(),
        confirmed_at=datetime.now(UTC),
        medications=[{"medication_name": "새 버전 합성약", "display_order": 1}],
    )

    with pytest.raises(ApiError) as detail_error:
        await service.get_guide_detail(user=owner, guide_id=guide.id)
    assert detail_error.value.status_code == 409
    assert detail_error.value.code == "PRESCRIPTION_VERSION_CONFLICT"

    with pytest.raises(ApiError) as latest_error:
        await service.get_latest_guide_for_prescription(user=owner, prescription_id=prescription.id)
    assert latest_error.value.status_code == 404
    assert latest_error.value.code == "GUIDE_NOT_FOUND"
