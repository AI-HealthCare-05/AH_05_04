"""RAG Candidate Identification 동시 확정 경계를 실제 PostgreSQL에서 검증합니다."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core import config
from app.core.db.databases import Base
from app.core.errors import ApiError
from app.dtos.medication_candidates import RejectMedicationCandidateRequest
from app.models.async_jobs import IdempotencyRecord
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import (
    MedicationCandidateSearch,
    MedicationCandidateSearchResult,
    MedicationCandidateSearchStatus,
    MedicationIdentification,
    MedicationIdentificationStatus,
)
from app.models.users import Gender, User
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.services.idempotency import SyncMutationIdempotencyService, get_default_snapshot_cipher
from app.services.medication_candidates import MedicationCandidateService
from app.services.medication_identification import MedicationIdentificationService
from app.tests.fixtures.prescription_fingerprint import fingerprint_values

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


async def _create_version_medication(
    session: AsyncSession,
    *,
    document_id: UUID,
    ocr_job_id: UUID,
    profile_id: UUID,
) -> PrescriptionVersionMedication:
    prescription_id = uuid4()
    version_id = uuid4()
    prescribed_date = date.today()
    confirmed_at = datetime.now(config.TIMEZONE)
    prescription = Prescription(
        id=prescription_id,
        active_version_id=version_id,
        document_id=document_id,
        source_ocr_job_id=ocr_job_id,
        profile_id=profile_id,
        prescribed_date=prescribed_date,
        confirmed_at=confirmed_at,
    )
    version = PrescriptionVersion(
        **fingerprint_values(
            prescribed_date, [{"medication_name": "테스트약", "strength_text": "500mg", "display_order": 1}]
        ),
        id=version_id,
        prescription_id=prescription_id,
        version_number=1,
        prescribed_date=prescribed_date,
        confirmed_at=confirmed_at,
    )
    version_medication = PrescriptionVersionMedication(
        medication_count=1,
        prescription_version_id=version_id,
        medication_name="테스트약",
        strength_text="500mg",
        display_order=1,
    )
    session.add_all([prescription, version, version_medication])
    await session.flush()
    return version_medication


async def _create_ready_search() -> tuple[UUID, UUID, UUID, UUID]:
    async with session_factory.begin() as session:
        # 이 helper는 여러 테스트에서 호출되므로(모듈 scope 격리 schema를 공유), 고정 이메일이면
        # 두 번째 호출이 unique 제약을 위반한다.
        owner = await _create_user(session, email=f"owner-{uuid4().hex[:8]}@example.com")
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

        version_medication = await _create_version_medication(
            session,
            document_id=document.id,
            ocr_job_id=ocr_job.id,
            profile_id=profile.id,
        )

        service = _service(session)
        search = (
            await service.record_candidate_search(
                prescription_version_medication_id=version_medication.id,
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
        return owner.id, version_medication.id, search.id, finalized.results[0].id


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

        version_medication = await _create_version_medication(
            session,
            document_id=document.id,
            ocr_job_id=ocr_job.id,
            profile_id=profile.id,
        )

        service = _service(session)
        replaced_search = (
            await service.record_candidate_search(
                prescription_version_medication_id=version_medication.id,
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
                prescription_version_medication_id=version_medication.id,
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
            version_medication.id,
            replaced_search.id,
            replaced_finalized.results[0].id,
            current_search.id,
            current_finalized.results[0].id,
        )


async def _assert_active_search_unique_index_exists() -> None:
    async with test_engine.begin() as connection:
        index_name = await connection.scalar(text("SELECT to_regclass('uq_medication_candidate_search_active')"))
    assert index_name is not None


async def _drop_active_search_unique_index() -> None:
    await _assert_active_search_unique_index_exists()
    async with test_engine.begin() as connection:
        await connection.execute(text("DROP INDEX uq_medication_candidate_search_active"))


async def _restore_active_search_unique_index() -> None:
    async with test_engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_medication_candidate_search_active "
                "ON medication_candidate_search (prescription_version_medication_id) "
                "WHERE status IN ('RUNNING', 'READY')"
            )
        )


async def _deactivate_searches_for_cleanup(search_ids: list[UUID]) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text(
                "UPDATE medication_candidate_search "
                "SET status = 'INVALIDATED_INPUT_CHANGED', invalidated_at = now() "
                "WHERE id = ANY(:search_ids) AND status IN ('RUNNING', 'READY')"
            ),
            {"search_ids": [str(search_id) for search_id in search_ids]},
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

        version_medication = await _create_version_medication(
            session,
            document_id=document.id,
            ocr_job_id=ocr_job.id,
            profile_id=profile.id,
        )

        searches: list[MedicationCandidateSearch] = []
        result_ids: list[UUID] = []
        for index in range(2):
            search = MedicationCandidateSearch(
                prescription_version_medication_id=version_medication.id,
                medication_name_snapshot=version_medication.medication_name,
                strength_text_snapshot=version_medication.strength_text,
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

        return owner.id, version_medication.id, searches[0].id, result_ids[0], searches[1].id, result_ids[1]


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


class _BarrierMedicationCandidateRepository(MedicationCandidateRepository):
    def __init__(
        self,
        session: AsyncSession,
        *,
        matched_precheck_barrier: asyncio.Barrier,
        precheck_none_count: list[UUID],
        insert_attempt_count: list[UUID],
        integrity_error_count: list[UUID],
    ) -> None:
        super().__init__(session)
        self._matched_precheck_barrier = matched_precheck_barrier
        self._precheck_none_count = precheck_none_count
        self._insert_attempt_count = insert_attempt_count
        self._integrity_error_count = integrity_error_count

    async def get_latest_matched_identification(
        self,
        *,
        prescription_version_medication_id: UUID,
    ) -> MedicationIdentification | None:
        existing = await super().get_latest_matched_identification(
            prescription_version_medication_id=prescription_version_medication_id
        )
        if existing is None:
            self._precheck_none_count.append(prescription_version_medication_id)
            await self._matched_precheck_barrier.wait()
        return existing

    async def create_matched_identification(
        self,
        *,
        prescription_version_medication_id: UUID,
        candidate_search: MedicationCandidateSearch,
        candidate_search_result: MedicationCandidateSearchResult,
        confirmed_at: datetime,
    ) -> MedicationIdentification:
        self._insert_attempt_count.append(candidate_search.id)
        try:
            return await super().create_matched_identification(
                prescription_version_medication_id=prescription_version_medication_id,
                candidate_search=candidate_search,
                candidate_search_result=candidate_search_result,
                confirmed_at=confirmed_at,
            )
        except IntegrityError:
            self._integrity_error_count.append(candidate_search.id)
            raise


async def _confirm_once_after_matched_precheck_barrier(
    *,
    user_id: UUID,
    medication_id: UUID,
    candidate_search_result_id: UUID,
    matched_precheck_barrier: asyncio.Barrier,
    precheck_none_count: list[UUID],
    insert_attempt_count: list[UUID],
    integrity_error_count: list[UUID],
) -> tuple[str, str | None]:
    async with session_factory() as session:
        repository = _BarrierMedicationCandidateRepository(
            session,
            matched_precheck_barrier=matched_precheck_barrier,
            precheck_none_count=precheck_none_count,
            insert_attempt_count=insert_attempt_count,
            integrity_error_count=integrity_error_count,
        )
        service = MedicationIdentificationService(repository)
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


_IDEMPOTENCY_CONFIRM_OPERATION_ID = "medication-candidate.confirm"


async def _confirm_once_with_idempotency(
    *,
    user_id: UUID,
    medication_id: UUID,
    candidate_search_result_id: UUID,
    idempotency_key: str,
) -> tuple[str, dict[str, Any] | None, bool | None]:
    """PR #346 리뷰 회귀: 동시 confirm 요청이 실제 도메인 경쟁(FOR UPDATE/unique index)으로
    패자가 ApiError(ALREADY_MATCHED)를 보게 되더라도, 같은 idempotency key·같은 지문이면
    idempotency 계층이 승자의 snapshot을 재현해야 한다 — 도메인 409로 끝나면 계약 위반이다."""
    async with session_factory() as session:
        identification_service = _service(session)
        idempotency_service = SyncMutationIdempotencyService(
            IdempotencyRepository(session), get_default_snapshot_cipher()
        )

        async def mutate() -> dict[str, Any]:
            identification = await identification_service.confirm_identification(
                prescription_version_medication_id=medication_id,
                candidate_search_result_id=candidate_search_result_id,
                user_id=user_id,
            )
            return {"identification_id": str(identification.id), "status": identification.status.value}

        try:
            result = await idempotency_service.execute(
                user_id=user_id,
                operation_id=_IDEMPOTENCY_CONFIRM_OPERATION_ID,
                parent_resource_id=medication_id,
                idempotency_key=idempotency_key,
                fingerprint={"candidate_search_result_id": str(candidate_search_result_id)},
                success_status=200,
                mutate=mutate,
            )
            await session.commit()
            return ("ok", result.response_body, result.is_replay)
        except ApiError as exc:
            await session.rollback()
            return (exc.code, None, None)


async def test_concurrent_confirm_with_same_idempotency_key_replays_winner_instead_of_domain_conflict() -> None:
    user_id, medication_id, search_id, result_id = await _create_ready_search()
    idempotency_key = "same-key-concurrency-test-" + uuid4().hex[:8]

    results = await asyncio.gather(
        _confirm_once_with_idempotency(
            user_id=user_id,
            medication_id=medication_id,
            candidate_search_result_id=result_id,
            idempotency_key=idempotency_key,
        ),
        _confirm_once_with_idempotency(
            user_id=user_id,
            medication_id=medication_id,
            candidate_search_result_id=result_id,
            idempotency_key=idempotency_key,
        ),
    )

    # 같은 key·같은 지문이므로 둘 다 성공해야 한다 — 도메인 409(ALREADY_MATCHED)로 끝나는
    # 요청이 하나라도 있으면 계약 위반이다.
    assert [code for code, _, _ in results] == ["ok", "ok"]
    bodies = [body for _, body, _ in results]
    assert bodies[0] == bodies[1]
    # 하나는 실제로 mutate()를 실행(is_replay=False)하고, 다른 하나는 그 snapshot을
    # 재현(is_replay=True)해야 한다 — 어느 쪽이 승자가 될지는 비결정적이라 순서로 확인하지 않는다.
    replay_flags: list[bool] = [is_replay for _, _, is_replay in results if is_replay is not None]
    assert len(replay_flags) == 2
    assert sorted(replay_flags) == [False, True]

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
        idempotency_records = (
            (
                await session.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.operation_id == _IDEMPOTENCY_CONFIRM_OPERATION_ID,
                        IdempotencyRecord.parent_resource_id == medication_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(identifications) == 1
    assert len(idempotency_records) == 1


async def _reject_once_with_idempotency(
    *,
    user_id: UUID,
    search_id: UUID,
    candidate_search_result_id: UUID,
    idempotency_key: str,
) -> tuple[str, dict[str, Any] | None, bool | None]:
    """reject_candidate는 confirm_candidate와 달리 idempotency 체크 전에
    `get_result_selection_for_update_owned`(FOR UPDATE)로 parent_resource_id를 먼저
    구하므로, 이 잠금이 동시 같은 key 요청을 완전히 직렬화해 도메인 충돌 자체가
    발생하지 않는지 실제로 검증한다(confirm과 동일한 문제가 reject에도 있는지 확인)."""
    async with session_factory() as session:
        repository = MedicationCandidateRepository(session)
        candidate_service = MedicationCandidateService(
            repository,
            _service(session),
            SyncMutationIdempotencyService(IdempotencyRepository(session), get_default_snapshot_cipher()),
        )

        try:
            result = await candidate_service.reject_candidate(
                user=SimpleNamespace(id=user_id),  # type: ignore[arg-type]
                request=RejectMedicationCandidateRequest(
                    search_id=search_id,
                    candidate_search_result_id=candidate_search_result_id,
                ),
                idempotency_key=idempotency_key,
            )
            await session.commit()
            return ("ok", result.model_dump(mode="json"), None)
        except ApiError as exc:
            await session.rollback()
            return (exc.code, None, None)


async def test_concurrent_reject_with_same_idempotency_key_does_not_hit_domain_conflict() -> None:
    user_id, medication_id, search_id, result_id = await _create_ready_search()
    idempotency_key = "same-key-reject-concurrency-test-" + uuid4().hex[:8]

    results = await asyncio.gather(
        _reject_once_with_idempotency(
            user_id=user_id, search_id=search_id, candidate_search_result_id=result_id, idempotency_key=idempotency_key
        ),
        _reject_once_with_idempotency(
            user_id=user_id, search_id=search_id, candidate_search_result_id=result_id, idempotency_key=idempotency_key
        ),
    )

    assert [code for code, _, _ in results] == ["ok", "ok"]
    bodies = [body for _, body, _ in results]
    assert bodies[0] == bodies[1]

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
        idempotency_records = (
            (
                await session.execute(
                    select(IdempotencyRecord).where(IdempotencyRecord.parent_resource_id == medication_id)
                )
            )
            .scalars()
            .all()
        )

    assert len(identifications) == 1
    assert len(idempotency_records) == 1


async def test_concurrent_confirm_allows_only_one_identification() -> None:
    user_id, medication_id, search_id, result_id = await _create_ready_search()

    results = await asyncio.gather(
        _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=result_id),
        _confirm_once(user_id=user_id, medication_id=medication_id, candidate_search_result_id=result_id),
    )

    assert results.count(("ok", None)) == 1
    assert any(
        code in {"CANDIDATE_SEARCH_STALE", "IDENTIFICATION_CONTEXT_STALE"} and reason in {"STALE", "ALREADY_MATCHED"}
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
    assert ("CANDIDATE_SEARCH_STALE", "STALE") in results

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
    first_search_id: UUID | None = None
    second_search_id: UUID | None = None
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

        results = await asyncio.wait_for(
            asyncio.gather(
                _confirm_once(
                    user_id=user_id,
                    medication_id=medication_id,
                    candidate_search_result_id=first_result_id,
                ),
                _confirm_once(
                    user_id=user_id,
                    medication_id=medication_id,
                    candidate_search_result_id=second_result_id,
                ),
            ),
            timeout=10,
        )

        assert results.count(("ok", None)) == 1
        assert any(
            (code == "IDENTIFICATION_CONTEXT_STALE" and reason == "ALREADY_MATCHED")
            or (code == "CANDIDATE_SEARCH_STALE" and reason == "STALE")
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
        search_ids = [search_id for search_id in (first_search_id, second_search_id) if search_id is not None]
        if search_ids:
            await _deactivate_searches_for_cleanup(search_ids)
        await _restore_active_search_unique_index()
