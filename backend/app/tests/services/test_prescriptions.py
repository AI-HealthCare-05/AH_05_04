from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.dtos.prescriptions import CorrectPrescriptionRequest, PrescriptionMedicationCorrectionRequest
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository
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
    session.add(
        PrescriptionVersionMedication(
            prescription_version_id=version_id,
            medication_name="타이레놀",
            display_order=1,
        )
    )
    await session.flush()

    return prescription


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
                    medication_name="정정된 합성약",
                    dose_value=Decimal("0.5"),
                    dose_unit="정",
                    frequency_per_day=2,
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
