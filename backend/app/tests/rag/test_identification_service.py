from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.errors import ApiError
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import (
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.models.users import Gender, User
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.services.medication_identification import MedicationIdentificationService
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


def _service(session: AsyncSession) -> MedicationIdentificationService:
    return MedicationIdentificationService(MedicationCandidateRepository(session))


def _ready_result(
    *,
    product_id=None,
    result_rank: int = 1,
    result_score: float = 0.95,
    result_method: str = "PRODUCT_NAME",
) -> MedicationCandidateResultCreate:
    return MedicationCandidateResultCreate(
        product_id=product_id or uuid4(),
        code_system="MFDS_ITEM_SEQ",
        canonical_code="200012345",
        product_name="테스트정",
        strength_text="500mg",
        dosage_form="정제",
        manufacturer_name="테스트제약",
        product_status="ACTIVE",
        result_rank=result_rank,
        result_score=result_score,
        result_method=result_method,
        is_displayed=True,
        selection_eligible=True,
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


async def _create_prescription(session: AsyncSession, *, user: User) -> Prescription:
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
        confirmed_at=datetime.now(config.TIMEZONE),
    )
    session.add(prescription)
    await session.flush()
    return prescription


async def _create_medication(
    session: AsyncSession, *, prescription: Prescription, display_order: int = 1
) -> Medication:
    medication = Medication(
        prescription_id=prescription.id,
        medication_name="테스트약",
        strength_text="500mg",
        display_order=display_order,
    )
    session.add(medication)
    await session.flush()
    return medication


async def test_record_candidate_search_reuses_same_context(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    bundle_id = uuid4()
    index_id = uuid4()
    expires_at = datetime.now(config.TIMEZONE) + timedelta(minutes=10)

    first = await service.record_candidate_search(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=bundle_id,
        candidate_index_version_id=index_id,
        expires_at=expires_at,
    )
    second = await service.record_candidate_search(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=bundle_id,
        candidate_index_version_id=index_id,
        expires_at=expires_at,
    )

    assert first.is_reused is False
    assert second.is_reused is True
    assert second.search.id == first.search.id


async def test_record_candidate_search_uses_owned_medication_snapshot(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="snapshot-owner@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    medication.medication_name = "서버확정약"
    medication.strength_text = "250mg"
    await db_session.flush()

    result = await service.record_candidate_search(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        query_digest="query-digest-server-snapshot",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )

    assert result.search.medication_name_snapshot == "서버확정약"
    assert result.search.strength_text_snapshot == "250mg"


async def test_record_candidate_search_rejects_other_users_medication(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="search-owner@example.com")
    intruder = await _create_user(db_session, email="search-intruder@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)

    with pytest.raises(ApiError) as exc_info:
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=intruder.id,
            query_digest="query-digest-cross-owner",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "CANDIDATE_SEARCH_NOT_FOUND"
    assert exc_info.value.details[0].field == "prescription_version_medication_id"


async def test_record_candidate_search_invalidates_changed_context(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner2@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)

    first = await service.record_candidate_search(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )
    second = await service.record_candidate_search(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        query_digest="query-digest-2",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )

    assert second.is_reused is False
    assert second.search.id != first.search.id
    assert first.search.status == MedicationCandidateSearchStatus.INVALIDATED_INPUT_CHANGED
    assert first.search.invalidated_at is not None


async def test_confirm_identification_consumes_ready_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner3@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    product_id = uuid4()
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result(product_id=product_id)],
    )

    identification = await service.confirm_identification(
        prescription_version_medication_id=medication.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    assert identification.status == MedicationIdentificationStatus.MATCHED
    assert identification.source == MedicationIdentificationSource.USER_SELECTED
    assert identification.product_id == product_id
    assert identification.confirmed_at is not None
    assert identification.decision_reason is None
    assert finalized.search.status == MedicationCandidateSearchStatus.CONSUMED
    assert finalized.search.consumed_at is not None


async def test_finalize_candidate_search_rejects_non_finite_result_score(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="non-finite-score@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search

    with pytest.raises(ApiError) as exc_info:
        await service.finalize_candidate_search(
            search_id=search.id,
            user_id=owner.id,
            status=MedicationCandidateSearchStatus.READY,
            results=[_ready_result(result_score=float("nan"))],
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_FAILED"
    assert exc_info.value.details[0].field == "result_score"
    assert exc_info.value.details[0].reason == "FINITE_NUMBER_REQUIRED"


async def test_finalize_candidate_search_rejects_blank_result_method(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="blank-method@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search

    with pytest.raises(ApiError) as exc_info:
        await service.finalize_candidate_search(
            search_id=search.id,
            user_id=owner.id,
            status=MedicationCandidateSearchStatus.READY,
            results=[_ready_result(result_method="  ")],
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "VALIDATION_FAILED"
    assert exc_info.value.details[0].field == "result_method"
    assert exc_info.value.details[0].reason == "NONBLANK_TEXT_REQUIRED"


async def test_confirm_identification_rejects_reconfirm_of_consumed_search(db_session: AsyncSession) -> None:
    """확인이 끝난 Search는 CONSUMED 상태이므로, 같은 후보를 다시 확인 요청해도 거부되어야 합니다."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner13@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )
    await service.confirm_identification(
        prescription_version_medication_id=medication.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.confirm_identification(
            prescription_version_medication_id=medication.id,
            candidate_search_result_id=finalized.results[0].id,
            user_id=owner.id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CANDIDATE_SEARCH_STALE"
    assert exc_info.value.details[0].reason == "SEARCH_NOT_READY"


async def test_confirm_identification_rejects_expired_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner17@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
        finalized_at=datetime.now(config.TIMEZONE),
    )
    finalized.search.expires_at = datetime.now(config.TIMEZONE) - timedelta(seconds=1)
    await db_session.flush()

    with pytest.raises(ApiError) as exc_info:
        await service.confirm_identification(
            prescription_version_medication_id=medication.id,
            candidate_search_result_id=finalized.results[0].id,
            user_id=owner.id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CANDIDATE_SEARCH_STALE"
    assert exc_info.value.details[0].reason == "SEARCH_EXPIRED"


async def test_confirm_identification_rejects_search_medication_mismatch(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner18@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    search_medication = await _create_medication(db_session, prescription=prescription, display_order=1)
    other_medication = await _create_medication(db_session, prescription=prescription, display_order=2)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=search_medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )

    with pytest.raises(ApiError) as exc_info:
        await service.confirm_identification(
            prescription_version_medication_id=other_medication.id,
            candidate_search_result_id=finalized.results[0].id,
            user_id=owner.id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CANDIDATE_SEARCH_STALE"
    assert exc_info.value.details[0].reason == "SEARCH_MEDICATION_MISMATCH"


async def test_record_candidate_search_rejects_existing_identification(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner4@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )
    await service.confirm_identification(
        prescription_version_medication_id=medication.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest-2",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )

    assert exc_info.value.code == "IDENTIFICATION_CONTEXT_STALE"
    assert exc_info.value.details[0].reason == "IDENTIFICATION_ALREADY_EXISTS"


async def test_reject_identification_invalidates_ready_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner5@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )

    identification = await service.reject_identification(
        search_id=search.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    assert identification.status == MedicationIdentificationStatus.UNRESOLVED
    assert identification.source == MedicationIdentificationSource.USER_REJECTED
    assert identification.product_id is None
    assert identification.confirmed_at is None
    assert identification.rejected_at is not None
    assert identification.decision_reason == "USER_REJECTED_DISPLAYED_CANDIDATE"
    assert finalized.search.status == MedicationCandidateSearchStatus.INVALIDATED_USER_REJECTED
    assert finalized.search.invalidated_at is not None


async def test_reject_identification_rejects_re_reject_of_invalidated_search(db_session: AsyncSession) -> None:
    """거절이 끝난 Search는 INVALIDATED_USER_REJECTED 상태이므로, 같은 후보를 다시 거절해도 거부되어야 합니다."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner14@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )
    await service.reject_identification(
        search_id=search.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.reject_identification(
            search_id=search.id,
            candidate_search_result_id=finalized.results[0].id,
            user_id=owner.id,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CANDIDATE_SEARCH_STALE"
    assert exc_info.value.details[0].reason == "SEARCH_NOT_READY"


async def test_record_candidate_search_rejects_after_user_rejected_identification(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner19@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )

    rejected = await service.reject_identification(
        search_id=search.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest-after-reject",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )

    identifications = (
        (
            await db_session.execute(
                select(MedicationIdentification).where(
                    MedicationIdentification.prescription_version_medication_id == medication.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [item.id for item in identifications] == [rejected.id]
    assert identifications[0].status == MedicationIdentificationStatus.UNRESOLVED
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "IDENTIFICATION_CONTEXT_STALE"
    assert exc_info.value.details[0].reason == "IDENTIFICATION_ALREADY_EXISTS"


async def test_expired_active_search_does_not_block_new_search(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner6@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    first = await service.record_candidate_search(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=datetime.now(config.TIMEZONE) - timedelta(seconds=1),
    )

    second = await service.record_candidate_search(
        prescription_version_medication_id=medication.id,
        user_id=owner.id,
        query_digest="query-digest-1",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )

    assert first.search.status == MedicationCandidateSearchStatus.EXPIRED
    assert second.is_reused is False
    assert second.search.id != first.search.id


async def test_non_ready_search_must_not_expose_selectable_result(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner7@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )
    ).search

    with pytest.raises(ApiError) as exc_info:
        await service.finalize_candidate_search(
            search_id=search.id,
            user_id=owner.id,
            status=MedicationCandidateSearchStatus.AMBIGUOUS,
            results=[_ready_result()],
        )

    assert exc_info.value.code == "VALIDATION_FAILED"
    assert exc_info.value.details[0].reason == "NON_READY_RESULT_MUST_NOT_BE_DISPLAYED"


async def test_preflight_passes_when_all_medications_are_matched(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner8@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    first_medication = await _create_medication(db_session, prescription=prescription, display_order=1)
    second_medication = await _create_medication(db_session, prescription=prescription, display_order=2)

    for medication in (first_medication, second_medication):
        search = (
            await service.record_candidate_search(
                prescription_version_medication_id=medication.id,
                user_id=owner.id,
                query_digest=f"query-digest-{medication.id}",
                runtime_release_bundle_id=None,
                candidate_index_version_id=None,
                expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
            )
        ).search
        finalized = await service.finalize_candidate_search(
            search_id=search.id,
            user_id=owner.id,
            status=MedicationCandidateSearchStatus.READY,
            results=[_ready_result()],
        )
        await service.confirm_identification(
            prescription_version_medication_id=medication.id,
            candidate_search_result_id=finalized.results[0].id,
            user_id=owner.id,
        )

    result = await service.ensure_matched_for_preflight(
        prescription_version_medication_ids=[first_medication.id, second_medication.id, first_medication.id]
    )

    assert result.prescription_version_medication_count == 2
    assert result.matched_identification_count == 2


async def test_preflight_rejects_when_any_medication_is_not_matched(db_session: AsyncSession) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner9@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    matched_medication = await _create_medication(db_session, prescription=prescription)
    unmatched_medication_id = uuid4()
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=matched_medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )
    await service.confirm_identification(
        prescription_version_medication_id=matched_medication.id,
        candidate_search_result_id=finalized.results[0].id,
        user_id=owner.id,
    )

    with pytest.raises(ApiError) as exc_info:
        await service.ensure_matched_for_preflight(
            prescription_version_medication_ids=[matched_medication.id, unmatched_medication_id]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "PRESCRIPTION_MEDICATION_IDENTIFICATION_INCOMPLETE"
    assert exc_info.value.details[0].field == "prescription_version_medication_ids"
    assert exc_info.value.details[0].reason == "MATCHED_IDENTIFICATION_REQUIRED"
    assert exc_info.value.details[0].rejected_value is None


async def test_finalize_candidate_search_rejects_other_users_search(db_session: AsyncSession) -> None:
    """다른 사용자의 Search를 search_id만 알면 최종화할 수 있으면 안 됩니다."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner11@example.com")
    intruder = await _create_user(db_session, email="intruder11@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=None,
        )
    ).search

    with pytest.raises(ApiError) as exc_info:
        await service.finalize_candidate_search(
            search_id=search.id,
            user_id=intruder.id,
            status=MedicationCandidateSearchStatus.READY,
            results=[_ready_result()],
        )

    assert exc_info.value.status_code == 404


async def test_confirm_and_reject_reject_other_users_result(db_session: AsyncSession) -> None:
    """다른 사용자의 candidate_search_result_id를 알아도 확인·거절할 수 없어야 합니다."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="owner12@example.com")
    intruder = await _create_user(db_session, email="intruder12@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)
    search = (
        await service.record_candidate_search(
            prescription_version_medication_id=medication.id,
            user_id=owner.id,
            query_digest="query-digest",
            runtime_release_bundle_id=None,
            candidate_index_version_id=None,
            expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
        )
    ).search
    finalized = await service.finalize_candidate_search(
        search_id=search.id,
        user_id=owner.id,
        status=MedicationCandidateSearchStatus.READY,
        results=[_ready_result()],
    )

    with pytest.raises(ApiError) as confirm_exc_info:
        await service.confirm_identification(
            prescription_version_medication_id=medication.id,
            candidate_search_result_id=finalized.results[0].id,
            user_id=intruder.id,
        )
    assert confirm_exc_info.value.status_code == 404

    with pytest.raises(ApiError) as reject_exc_info:
        await service.reject_identification(
            search_id=search.id,
            candidate_search_result_id=finalized.results[0].id,
            user_id=intruder.id,
        )
    assert reject_exc_info.value.status_code == 404
