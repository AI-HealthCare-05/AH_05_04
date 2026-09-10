from collections.abc import AsyncIterator
from datetime import date, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.tests.conftest import test_engine
from app.tests.fixtures.prescription_fingerprint import fingerprint_values


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


def _ready_result(*, product_id=None, result_rank: int = 1) -> MedicationCandidateResultCreate:
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
        result_score=0.95,
        result_method="PRODUCT_NAME",
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


async def _create_prescription(session: AsyncSession, *, user: User, medication_count: int = 1) -> Prescription:
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
            **fingerprint_values(
                prescription.prescribed_date,
                [
                    {"medication_name": "테스트약", "strength_text": "500mg", "display_order": i}
                    for i in range(1, medication_count + 1)
                ],
            ),
            id=version_id,
            prescription_id=prescription.id,
            version_number=1,
            prescribed_date=prescription.prescribed_date,
            confirmed_at=prescription.confirmed_at,
        )
    )
    await session.flush()
    return prescription


async def _create_medication(
    session: AsyncSession, *, prescription: Prescription, display_order: int = 1
) -> PrescriptionVersionMedication:
    assert prescription.active_version_id is not None
    version = await session.get(PrescriptionVersion, prescription.active_version_id)
    medication = PrescriptionVersionMedication(
        medication_count=version.medication_count,
        prescription_version_id=prescription.active_version_id,
        medication_name="테스트약",
        strength_text="500mg",
        display_order=display_order,
    )
    session.add(medication)
    await session.flush()
    return medication


async def _create_search(
    repository: MedicationCandidateRepository, *, prescription: Prescription, display_order: int = 1
):
    medication = await _create_medication(repository.session, prescription=prescription, display_order=display_order)
    return await repository.create_search(
        prescription_version_medication_id=medication.id,
        medication_name_snapshot="테스트약",
        strength_text_snapshot="500mg",
        query_digest="query-digest",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )


async def test_search_cannot_have_multiple_displayed_or_selectable_results(db_session: AsyncSession) -> None:
    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="owner10@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    search = await _create_search(repository, prescription=prescription)

    with pytest.raises(IntegrityError):
        await repository.add_results(
            search=search,
            results=[
                _ready_result(result_rank=1),
                _ready_result(result_rank=2),
            ],
        )


async def test_get_search_for_update_owned_rejects_other_users_search(db_session: AsyncSession) -> None:
    """search_id만 알아도 다른 사용자의 Search는 owned 조회로 가져올 수 없어야 합니다."""
    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="owner15@example.com")
    intruder = await _create_user(db_session, email="intruder15@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    search = await _create_search(repository, prescription=prescription)

    owned = await repository.get_search_for_update_owned(search_id=search.id, user_id=owner.id)
    assert owned is not None

    stolen = await repository.get_search_for_update_owned(search_id=search.id, user_id=intruder.id)
    assert stolen is None


async def test_get_medication_owned_rejects_other_users_medication(db_session: AsyncSession) -> None:
    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="owner20@example.com")
    intruder = await _create_user(db_session, email="intruder20@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    medication = await _create_medication(db_session, prescription=prescription)

    owned = await repository.get_medication_owned(prescription_version_medication_id=medication.id, user_id=owner.id)
    assert owned is not None
    assert owned.id == medication.id

    stolen = await repository.get_medication_owned(
        prescription_version_medication_id=medication.id, user_id=intruder.id
    )
    assert stolen is None


async def test_get_latest_search_for_medication_returns_most_recent(db_session: AsyncSession) -> None:
    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="owner21@example.com")
    prescription = await _create_prescription(db_session, user=owner, medication_count=2)
    medication = await _create_medication(db_session, prescription=prescription)

    first = await _create_search(repository, prescription=prescription, display_order=2)
    # 같은 트랜잭션 안에서는 func.now()가 같은 값을 반환하므로, 최신 판정을 명시적으로 재현합니다.
    first.created_at = datetime(2026, 1, 1, tzinfo=config.TIMEZONE)
    await db_session.flush()

    latest = await repository.create_search(
        prescription_version_medication_id=medication.id,
        medication_name_snapshot="테스트약",
        strength_text_snapshot="500mg",
        query_digest="query-digest-latest",
        runtime_release_bundle_id=None,
        candidate_index_version_id=None,
        expires_at=None,
    )

    result = await repository.get_latest_search_for_medication(prescription_version_medication_id=medication.id)

    assert result is not None
    assert result.id == latest.id


async def test_get_latest_search_for_medication_returns_none_when_absent(db_session: AsyncSession) -> None:
    repository = MedicationCandidateRepository(db_session)
    assert await repository.get_latest_search_for_medication(prescription_version_medication_id=uuid4()) is None


async def test_get_displayed_result_for_search_returns_only_displayed_row(db_session: AsyncSession) -> None:
    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="owner22@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    search = await _create_search(repository, prescription=prescription)
    results = await repository.add_results(
        search=search,
        results=[
            MedicationCandidateResultCreate(
                product_id=None,
                code_system=None,
                canonical_code=None,
                product_name=None,
                strength_text=None,
                dosage_form=None,
                manufacturer_name=None,
                product_status=None,
                result_rank=2,
                result_score=0.4,
                result_method="TRIGRAM",
                is_displayed=False,
                selection_eligible=False,
            ),
            _ready_result(result_rank=1),
        ],
    )

    displayed = await repository.get_displayed_result_for_search(search_id=search.id)

    assert displayed is not None
    assert displayed.id == next(r.id for r in results if r.is_displayed)


async def test_get_displayed_result_for_search_returns_none_when_none_displayed(db_session: AsyncSession) -> None:
    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="owner23@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    search = await _create_search(repository, prescription=prescription)

    assert await repository.get_displayed_result_for_search(search_id=search.id) is None


async def test_get_result_selection_for_update_owned_rejects_other_users_result(db_session: AsyncSession) -> None:
    """candidate_search_result_id만 알아도 다른 사용자의 Result는 owned 조회로 가져올 수 없어야 합니다."""
    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="owner16@example.com")
    intruder = await _create_user(db_session, email="intruder16@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    search = await _create_search(repository, prescription=prescription)
    results = await repository.add_results(search=search, results=[_ready_result()])

    owned = await repository.get_result_selection_for_update_owned(
        candidate_search_result_id=results[0].id, user_id=owner.id
    )
    assert owned is not None

    stolen = await repository.get_result_selection_for_update_owned(
        candidate_search_result_id=results[0].id, user_id=intruder.id
    )
    assert stolen is None


async def test_finalization_failure_rolls_back_results_and_allows_clean_retry(db_session, monkeypatch):
    from unittest.mock import AsyncMock

    from sqlalchemy import func

    from app.models.rag_candidate import (
        MedicationCandidateSearch,
        MedicationCandidateSearchResult,
        MedicationCandidateSearchStatus,
    )

    repository = MedicationCandidateRepository(db_session)
    owner = await _create_user(db_session, email="synthetic-candidate398@example.com")
    prescription = await _create_prescription(db_session, user=owner)
    search = await _create_search(repository, prescription=prescription)
    search_id = search.id
    original = repository.finalize_search
    monkeypatch.setattr(repository, "finalize_search", AsyncMock(side_effect=ValueError("synthetic count mismatch")))
    with pytest.raises(ValueError, match="synthetic count mismatch"):
        await repository.assemble_and_finalize_search(
            search=search,
            results=[_ready_result()],
            status=MedicationCandidateSearchStatus.READY,
            finalized_at=datetime.now(config.TIMEZONE),
        )
    await db_session.commit()
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MedicationCandidateSearchResult)
            .where(MedicationCandidateSearchResult.search_id == search_id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(MedicationCandidateSearch.status).where(MedicationCandidateSearch.id == search_id)
        )
        == MedicationCandidateSearchStatus.RUNNING
    )
    monkeypatch.setattr(repository, "finalize_search", original)
    finalized, results = await repository.assemble_and_finalize_search(
        search=search,
        results=[_ready_result()],
        status=MedicationCandidateSearchStatus.READY,
        finalized_at=datetime.now(config.TIMEZONE),
    )
    assert finalized.candidate_count == finalized.displayed_candidate_count == len(results) == 1
    with pytest.raises(ValueError, match="must be running"):
        await repository.assemble_and_finalize_search(
            search=search,
            results=[_ready_result()],
            status=MedicationCandidateSearchStatus.READY,
            finalized_at=datetime.now(config.TIMEZONE),
        )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MedicationCandidateSearchResult)
            .where(MedicationCandidateSearchResult.search_id == search_id)
        )
        == 1
    )
