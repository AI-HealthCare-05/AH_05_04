from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError
from app.dtos.medication_candidates import (
    ConfirmMedicationCandidateRequest,
    RejectMedicationCandidateRequest,
)
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import MedicationCandidateSearchStatus as ModelSearchStatus
from app.models.users import Gender, User
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.services.medication_candidates import MedicationCandidateService
from app.services.medication_identification import MedicationIdentificationService
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


def _service(session: AsyncSession) -> MedicationCandidateService:
    repository = MedicationCandidateRepository(session)
    return MedicationCandidateService(repository, MedicationIdentificationService(repository))


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


async def _create_medication(
    session: AsyncSession, *, user: User, display_order: int = 1
) -> PrescriptionVersionMedication:
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
        confirmed_at=datetime.now(config.TIMEZONE),
    )
    session.add(prescription)
    await session.flush()
    session.add(
        PrescriptionVersion(
            id=version_id,
            prescription_id=prescription.id,
            version_number=1,
            prescribed_date=prescription.prescribed_date,
            confirmed_at=prescription.confirmed_at,
        )
    )
    await session.flush()

    medication = PrescriptionVersionMedication(
        prescription_version_id=version_id,
        medication_name="테스트약",
        strength_text="500mg",
        display_order=display_order,
    )
    session.add(medication)
    await session.flush()
    return medication


async def _create_ready_search(session: AsyncSession, *, medication: PrescriptionVersionMedication, user: User):
    identification_service = MedicationIdentificationService(MedicationCandidateRepository(session))
    search = (
        await identification_service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=user.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    return await identification_service.finalize_candidate_search(
        search_id=search.id,
        user_id=user.id,
        status=ModelSearchStatus.READY,
        results=[
            MedicationCandidateResultCreate(
                product_id=uuid4(),
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012345",
                product_name="테스트정",
                strength_text="500mg",
                dosage_form="정제",
                manufacturer_name="테스트제약",
                product_status="ACTIVE",
                result_rank=1,
                result_score=0.95,
                result_method="PRODUCT_NAME",
                is_displayed=True,
                selection_eligible=True,
            )
        ],
    )


async def test_get_candidate_search_rejects_unknown_medication(db_session: AsyncSession) -> None:
    service = _service(db_session)

    with pytest.raises(ApiError) as exc_info:
        await service.get_candidate_search(
            user=await _create_user(db_session, email="get1@example.com"),
            prescription_version_medication_id=uuid4(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PRESCRIPTION_MEDICATION_NOT_FOUND"


async def test_get_candidate_search_rejects_medication_without_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="get2@example.com")
    medication = await _create_medication(db_session, user=owner)

    with pytest.raises(ApiError) as exc_info:
        await service.get_candidate_search(user=owner, prescription_version_medication_id=medication.id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "CANDIDATE_SEARCH_NOT_FOUND"


async def test_get_candidate_search_returns_ready_snapshot_and_medication_index(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="get3@example.com")
    medication = await _create_medication(db_session, user=owner, display_order=3)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)

    result = await service.get_candidate_search(user=owner, prescription_version_medication_id=medication.id)

    assert result.search_id == finalized.search.id
    assert result.medication_index == 3
    assert result.status.value == "READY"
    assert result.candidate_search_result_id == finalized.results[0].id
    assert result.candidate is not None
    assert result.candidate.product_name == "테스트정"


async def test_get_candidate_search_projects_expired_ready_search_as_expired(db_session: AsyncSession) -> None:
    """만료 시각이 지난 READY Search는, 다른 lifecycle 요청이 아직 DB 상태를 EXPIRED로
    전환하기 전이라도 조회 응답에서는 candidate 없는 EXPIRED로 보여야 합니다(#312 리뷰 지적)."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="get5@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)
    finalized.search.expires_at = datetime.now(config.TIMEZONE) - timedelta(seconds=1)
    await db_session.flush()

    result = await service.get_candidate_search(user=owner, prescription_version_medication_id=medication.id)

    assert result.status.value == "EXPIRED"
    assert result.candidate_search_result_id is None
    assert result.candidate is None


async def test_get_candidate_search_hides_candidate_after_rejection(db_session: AsyncSession) -> None:
    """거절된 Search는 감사 이력상 is_displayed=true를 보존하지만(계약 111행), 공개 조회
    응답에는 candidate/ID가 노출되면 안 됩니다."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="get4@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)
    identification_service = MedicationIdentificationService(MedicationCandidateRepository(db_session))
    await identification_service.reject_identification(
        search_id=finalized.search.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    result = await service.get_candidate_search(user=owner, prescription_version_medication_id=medication.id)

    assert result.status.value == "INVALIDATED_USER_REJECTED"
    assert result.candidate_search_result_id is None
    assert result.candidate is None


async def test_confirm_candidate_maps_identification_data(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="confirm1@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)

    result = await service.confirm_candidate(
        user=owner,
        request=ConfirmMedicationCandidateRequest(
            prescription_version_medication_id=medication.id,
            candidate_search_result_id=finalized.results[0].id,
        ),
    )

    assert result.prescription_version_medication_id == medication.id
    assert result.status.value == "MATCHED"
    assert result.source.value == "USER_SELECTED"
    assert result.product_id is not None
    assert result.confirmed_at is not None


async def test_reject_candidate_maps_identification_event_data(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="reject1@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)

    result = await service.reject_candidate(
        user=owner,
        request=RejectMedicationCandidateRequest(
            search_id=finalized.search.id,
            candidate_search_result_id=finalized.results[0].id,
        ),
    )

    assert result.identification_event_id is not None
    assert result.prescription_version_medication_id == medication.id
    assert result.status.value == "UNRESOLVED"
    assert result.search_status.value == "INVALIDATED_USER_REJECTED"
    assert result.rejected_at is not None
