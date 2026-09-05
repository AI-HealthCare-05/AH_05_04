"""RAG Candidate Identification 동시 확정 경계를 실제 PostgreSQL에서 검증합니다."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core import config
from app.core.db.databases import Base
from app.core.errors import ApiError
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationStatus,
)
from app.models.users import Gender, User
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.services.medication_identification import MedicationIdentificationService

pytestmark = pytest.mark.asyncio

TEST_SCHEMA = "rag_identification_concurrency_test"
TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database="test",
)
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
)
session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def isolated_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


def _service(session: AsyncSession) -> MedicationIdentificationService:
    return MedicationIdentificationService(MedicationCandidateRepository(session))


def _ready_result(*, product_id: UUID | None = None) -> MedicationCandidateResultCreate:
    return MedicationCandidateResultCreate(
        product_id=product_id or uuid4(),
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
    session.add(Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name))
    await session.flush()
    return user


async def _create_ready_search() -> tuple[UUID, UUID, UUID, UUID]:
    async with session_factory.begin() as session:
        owner = await _create_user(session, email="owner@example.com")
        profile = await session.scalar(
            select(Profile).where(Profile.user_id == owner.id, Profile.profile_type == ProfileType.SELF)
        )
        assert profile is not None
        document = MedicalDocument(
            uploaded_by=owner.id,
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

        medication = Medication(
            prescription_id=prescription.id,
            medication_name="테스트약",
            strength_text="500mg",
            display_order=1,
        )
        session.add(medication)
        await session.flush()

        service = _service(session)
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
        return owner.id, medication.id, search.id, finalized.results[0].id


async def _create_replaced_and_current_ready_searches() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    async with session_factory.begin() as session:
        owner = await _create_user(session, email="different-search-owner@example.com")
        profile = await session.scalar(
            select(Profile).where(Profile.user_id == owner.id, Profile.profile_type == ProfileType.SELF)
        )
        assert profile is not None
        document = MedicalDocument(
            uploaded_by=owner.id,
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

        medication = Medication(
            prescription_id=prescription.id,
            medication_name="테스트약",
            strength_text="500mg",
            display_order=1,
        )
        session.add(medication)
        await session.flush()

        service = _service(session)
        replaced_search = (
            await service.record_candidate_search(
                prescription_version_medication_id=medication.id,
                user_id=owner.id,
                query_digest="query-digest-old",
                runtime_release_bundle_id=None,
                candidate_index_version_id=None,
                expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
            )
        ).search
        replaced_finalized = await service.finalize_candidate_search(
            search_id=replaced_search.id,
            user_id=owner.id,
            status=MedicationCandidateSearchStatus.READY,
            results=[_ready_result()],
        )

        current_search = (
            await service.record_candidate_search(
                prescription_version_medication_id=medication.id,
                user_id=owner.id,
                query_digest="query-digest-current",
                runtime_release_bundle_id=None,
                candidate_index_version_id=None,
                expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
            )
        ).search
        current_finalized = await service.finalize_candidate_search(
            search_id=current_search.id,
            user_id=owner.id,
            status=MedicationCandidateSearchStatus.READY,
            results=[_ready_result()],
        )
        return (
            owner.id,
            medication.id,
            replaced_search.id,
            replaced_finalized.results[0].id,
            current_search.id,
            current_finalized.results[0].id,
        )


async def _drop_active_search_unique_index() -> None:
    async with test_engine.begin() as connection:
        await connection.execute(text("DROP INDEX IF EXISTS uq_medication_candidate_search_active"))


async def _restore_active_search_unique_index() -> None:
    async with test_engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_medication_candidate_search_active "
                "ON medication_candidate_search (prescription_version_medication_id) "
                "WHERE status IN ('RUNNING', 'READY')"
            )
        )


async def _create_two_ready_searches_for_same_medication() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID]:
    """비정상 DB 상태에서도 medication당 MATCHED Identification은 1개만 허용되는지 검증합니다.

    운영 schema는 active Search unique index로 두 READY Search를 먼저 차단합니다.
    이 테스트는 그 방어선 바깥에서 직접 DB로 잘못된 상태가 생겨도
    Identification partial unique index가 마지막 방어선으로 작동하는지 보기 위해
    격리 schema 안에서만 active Search index를 잠시 내립니다.
    """
    async with session_factory.begin() as session:
        owner = await _create_user(session, email="two-ready-owner@example.com")
        profile = await session.scalar(
            select(Profile).where(Profile.user_id == owner.id, Profile.profile_type == ProfileType.SELF)
        )
        assert profile is not None
        document = MedicalDocument(
            uploaded_by=owner.id,
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

        medication = Medication(
            prescription_id=prescription.id,
            medication_name="테스트약",
            strength_text="500mg",
            display_order=1,
        )
        session.add(medication)
        await session.flush()

        searches: list[MedicationCandidateSearch] = []
        result_ids: list[UUID] = []
        for index in range(2):
            search = MedicationCandidateSearch(
                prescription_version_medication_id=medication.id,
                medication_name_snapshot=medication.medication_name,
                strength_text_snapshot=medication.strength_text,
                query_digest=f"query-digest-two-ready-{index}",
                runtime_release_bundle_id=None,
                candidate_index_version_id=None,
                status=MedicationCandidateSearchStatus.READY,
                candidate_count=1,
                displayed_candidate_count=1,
                expires_at=datetime.now(config.TIMEZONE) + timedelta(minutes=10),
                finalized_at=datetime.now(config.TIMEZONE),
            )
            session.add(search)
            await session.flush()

            result = MedicationCandidateSearchResult(
                search_id=search.id,
                product_id=uuid4(),
                code_system="MFDS_ITEM_SEQ",
                canonical_code=f"20001234{index}",
                product_name=f"테스트정{index}",
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
            session.add(result)
            await session.flush()

            searches.append(search)
            result_ids.append(result.id)

        return owner.id, medication.id, searches[0].id, result_ids[0], searches[1].id, result_ids[1]


async def _confirm_once(
    *,
    user_id: UUID,
    medication_id: UUID,
    candidate_search_result_id: UUID,
) -> tuple[str, str | None]:
    async with session_factory() as session:
        service = _service(session)
        try:
            await service.confirm_identification(
                prescription_version_medication_id=medication_id,
                candidate_search_result_id=candidate_search_result_id,
                user_id=user_id,
            )
            await session.commit()
            return ("ok", None)
        except ApiError as exc:
            await session.rollback()
            reason = exc.details[0].reason if exc.details else None
            return (exc.code, reason)


async def test_concurrent_confirm_allows_only_one_identification() -> None:
    user_id, medication_id, search_id, result_id = await _create_ready_search()

    results = await asyncio.gather(
        _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=result_id),
        _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=result_id),
    )

    assert results.count(("ok", None)) == 1
    assert any(
        code in {"CANDIDATE_SEARCH_STALE", "IDENTIFICATION_CONTEXT_STALE"}
        and reason in {"SEARCH_NOT_READY", "ALREADY_MATCHED"}
        for code, reason in results
    )

    async with session_factory() as session:
        identifications = (
            (
                await session.execute(
                    select(MedicationIdentification).where(
                        MedicationIdentification.prescription_version_medication_id == medication_id
                    )
                )
            )
            .scalars()
            .all()
        )
        search_status = await session.scalar(
            select(MedicationCandidateSearch.status).where(MedicationCandidateSearch.id == search_id)
        )

    assert len(identifications) == 1
    assert identifications[0].status == MedicationIdentificationStatus.MATCHED
    assert identifications[0].candidate_search_id == search_id
    assert search_status == MedicationCandidateSearchStatus.CONSUMED


async def test_concurrent_confirm_different_searches_allows_only_current_search() -> None:
    (
        user_id,
        medication_id,
        replaced_search_id,
        replaced_result_id,
        current_search_id,
        current_result_id,
    ) = await _create_replaced_and_current_ready_searches()

    results = await asyncio.gather(
        _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=replaced_result_id),
        _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=current_result_id),
    )

    assert results.count(("ok", None)) == 1
    assert ("CANDIDATE_SEARCH_STALE", "SEARCH_NOT_READY") in results

    async with session_factory() as session:
        identifications = (
            (
                await session.execute(
                    select(MedicationIdentification).where(
                        MedicationIdentification.prescription_version_medication_id == medication_id
                    )
                )
            )
            .scalars()
            .all()
        )
        search_status_rows = (
            await session.execute(
                select(MedicationCandidateSearch.id, MedicationCandidateSearch.status).where(
                    MedicationCandidateSearch.id.in_([replaced_search_id, current_search_id])
                )
            )
        ).all()
        search_statuses: dict[UUID, MedicationCandidateSearchStatus] = {row[0]: row[1] for row in search_status_rows}

    assert len(identifications) == 1
    assert identifications[0].status == MedicationIdentificationStatus.MATCHED
    assert identifications[0].candidate_search_id == current_search_id
    assert search_statuses[replaced_search_id] == MedicationCandidateSearchStatus.INVALIDATED_INPUT_CHANGED
    assert search_statuses[current_search_id] == MedicationCandidateSearchStatus.CONSUMED


async def test_concurrent_confirm_two_ready_searches_allows_only_one_matched_identification() -> None:
    await _drop_active_search_unique_index()
    try:
        (
            user_id,
            medication_id,
            first_search_id,
            first_result_id,
            second_search_id,
            second_result_id,
        ) = await _create_two_ready_searches_for_same_medication()

        results = await asyncio.gather(
            _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=first_result_id),
            _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=second_result_id),
        )

        assert results.count(("ok", None)) == 1
        assert any(
            code in {"CANDIDATE_SEARCH_STALE", "IDENTIFICATION_CONTEXT_STALE"} and reason == "ALREADY_MATCHED"
            for code, reason in results
        )

        async with session_factory() as session:
            identifications = (
                (
                    await session.execute(
                        select(MedicationIdentification).where(
                            MedicationIdentification.prescription_version_medication_id == medication_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            search_status_rows = (
                await session.execute(
                    select(MedicationCandidateSearch.id, MedicationCandidateSearch.status).where(
                        MedicationCandidateSearch.id.in_([first_search_id, second_search_id])
                    )
                )
            ).all()
            search_statuses: dict[UUID, MedicationCandidateSearchStatus] = {
                row[0]: row[1] for row in search_status_rows
            }

        assert len(identifications) == 1
        assert identifications[0].status == MedicationIdentificationStatus.MATCHED
        assert identifications[0].candidate_search_id in {first_search_id, second_search_id}
        assert list(search_statuses.values()).count(MedicationCandidateSearchStatus.CONSUMED) == 1
        assert list(search_statuses.values()).count(MedicationCandidateSearchStatus.READY) == 1
    finally:
        await _restore_active_search_unique_index()
