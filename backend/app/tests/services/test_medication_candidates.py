from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

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
from app.models.async_jobs import IdempotencyRecord
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.profiles import Profile, ProfileType
from app.models.rag_candidate import MedicationCandidateSearchStatus as ModelSearchStatus
from app.models.rag_candidate import MedicationIdentification
from app.models.users import Gender, User
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.medication_candidate_repository import (
    MedicationCandidateRepository,
    MedicationCandidateResultCreate,
)
from app.services.idempotency import (
    IdempotencyKeyConflictError,
    IdempotencyResponseTooLargeError,
    SyncMutationIdempotencyService,
)
from app.services.medication_candidates import MedicationCandidateService
from app.services.medication_identification import MedicationIdentificationService
from app.tests.conftest import test_engine


async def _count_identifications_for_medication(session: AsyncSession, *, medication_id: UUID) -> int:
    result = await session.execute(
        select(MedicationIdentification).where(
            MedicationIdentification.prescription_version_medication_id == medication_id
        )
    )
    return len(result.scalars().all())


async def _count_idempotency_records(session: AsyncSession) -> int:
    result = await session.execute(select(IdempotencyRecord))
    return len(result.scalars().all())


class _OversizedCipher:
    """snapshot cap 초과 경로만 재현하기 위한 테스트 전용 cipher입니다 — 입력과 무관하게
    1MiB를 넘는 ciphertext를 돌려줍니다."""

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        return b"x" * (1024 * 1024 + 1), "test-v1"

    def decrypt(self, ciphertext: bytes, *, key_version: str) -> bytes:
        raise AssertionError("이 테스트에서는 decrypt가 호출되면 안 됩니다")


class _FakeCipher:
    """테스트 전용 reversible cipher입니다 — 실제 암호화가 아닙니다."""

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        return plaintext[::-1], "test-v1"

    def decrypt(self, ciphertext: bytes, *, key_version: str) -> bytes:
        assert key_version == "test-v1"
        return ciphertext[::-1]


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
    idempotency_service = SyncMutationIdempotencyService(IdempotencyRepository(session), _FakeCipher())
    return MedicationCandidateService(repository, MedicationIdentificationService(repository), idempotency_service)


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


async def _create_medication(session: AsyncSession, *, user: User, display_order: int = 1) -> Medication:
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

    medication = Medication(
        prescription_id=prescription.id,
        medication_name="테스트약",
        strength_text="500mg",
        display_order=display_order,
    )
    session.add(medication)
    await session.flush()
    return medication


async def _create_ready_search(session: AsyncSession, *, medication: Medication, user: User):
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
        idempotency_key="a" * 32,
    )

    assert result.prescription_version_medication_id == medication.id
    assert result.status.value == "MATCHED"
    assert result.source.value == "USER_SELECTED"
    assert result.product_id is not None
    assert result.confirmed_at is not None


async def test_confirm_candidate_replays_stored_response_for_same_key_and_request(
    db_session: AsyncSession,
) -> None:
    """같은 Idempotency-Key와 같은 요청이 재시도되면 confirm_identification을 다시
    실행하지 않고 저장된 응답을 그대로 재현해야 한다(idempotency-v1.md). mock/spy로
    실제 도메인 로직(confirm_identification)이 두 번째 호출에서 재호출되지 않는지 확인한다."""
    repository = MedicationCandidateRepository(db_session)
    identification_service = MedicationIdentificationService(repository)
    idempotency_service = SyncMutationIdempotencyService(IdempotencyRepository(db_session), _FakeCipher())
    service = MedicationCandidateService(repository, identification_service, idempotency_service)
    owner = await _create_user(db_session, email="confirm2@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)
    request = ConfirmMedicationCandidateRequest(
        prescription_version_medication_id=medication.id,
        candidate_search_result_id=finalized.results[0].id,
    )

    spy = AsyncMock(wraps=identification_service.confirm_identification)
    identification_service.confirm_identification = spy  # type: ignore[method-assign]

    first = await service.confirm_candidate(user=owner, request=request, idempotency_key="b" * 32)
    second = await service.confirm_candidate(user=owner, request=request, idempotency_key="b" * 32)

    assert spy.await_count == 1
    assert second.identification_id == first.identification_id
    assert second.confirmed_at == first.confirmed_at
    assert await _count_identifications_for_medication(db_session, medication_id=medication.id) == 1


async def test_confirm_candidate_rejects_same_key_with_different_candidate_as_conflict(
    db_session: AsyncSession,
) -> None:
    """동일 key·다른 지문은 409이며, 실패한 두 번째 요청은 도메인 side-effect를 전혀
    남기지 않아야 한다(재실행되지 않았으므로 identification 행이 1건만 남는다)."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="confirm3@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)
    idempotency_key = "c" * 32

    await service.confirm_candidate(
        user=owner,
        request=ConfirmMedicationCandidateRequest(
            prescription_version_medication_id=medication.id,
            candidate_search_result_id=finalized.results[0].id,
        ),
        idempotency_key=idempotency_key,
    )

    with pytest.raises(IdempotencyKeyConflictError):
        await service.confirm_candidate(
            user=owner,
            request=ConfirmMedicationCandidateRequest(
                prescription_version_medication_id=medication.id,
                candidate_search_result_id=uuid4(),
            ),
            idempotency_key=idempotency_key,
        )

    assert await _count_identifications_for_medication(db_session, medication_id=medication.id) == 1
    assert await _count_idempotency_records(db_session) == 1


async def test_confirm_candidate_does_not_persist_record_when_result_not_found(
    db_session: AsyncSession,
) -> None:
    """4xx 응답(존재하지 않는 candidate_search_result_id)은 idempotency record를
    저장하지 않아야 한다 — 같은 key로 유효한 요청을 다시 보내면 정상 처리되어야 한다."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="confirm4@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)
    idempotency_key = "e" * 32

    with pytest.raises(ApiError) as exc_info:
        await service.confirm_candidate(
            user=owner,
            request=ConfirmMedicationCandidateRequest(
                prescription_version_medication_id=medication.id,
                candidate_search_result_id=uuid4(),
            ),
            idempotency_key=idempotency_key,
        )
    assert exc_info.value.status_code == 404
    assert await _count_idempotency_records(db_session) == 0

    # 같은 key로 이번엔 유효한 요청을 보내면(4xx는 저장하지 않았으므로) 정상 처리돼야 한다.
    result = await service.confirm_candidate(
        user=owner,
        request=ConfirmMedicationCandidateRequest(
            prescription_version_medication_id=medication.id,
            candidate_search_result_id=finalized.results[0].id,
        ),
        idempotency_key=idempotency_key,
    )
    assert result.status.value == "MATCHED"
    assert await _count_idempotency_records(db_session) == 1


async def test_confirm_candidate_rolls_back_confirmation_when_snapshot_exceeds_cap(
    db_session: AsyncSession,
) -> None:
    """snapshot이 1MiB cap을 초과하면 503으로 끝나야 하고, 이미 실행된 confirm_identification의
    DB 변경(MATCHED identification 생성)도 같은 트랜잭션에서 함께 롤백돼야 한다."""
    repository = MedicationCandidateRepository(db_session)
    identification_service = MedicationIdentificationService(repository)
    idempotency_service = SyncMutationIdempotencyService(IdempotencyRepository(db_session), _OversizedCipher())
    service = MedicationCandidateService(repository, identification_service, idempotency_service)
    owner = await _create_user(db_session, email="confirm5@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)

    with pytest.raises(IdempotencyResponseTooLargeError):
        await service.confirm_candidate(
            user=owner,
            request=ConfirmMedicationCandidateRequest(
                prescription_version_medication_id=medication.id,
                candidate_search_result_id=finalized.results[0].id,
            ),
            idempotency_key="f" * 32,
        )

    assert await _count_idempotency_records(db_session) == 0
    assert await _count_identifications_for_medication(db_session, medication_id=medication.id) == 0


async def test_confirm_candidate_isolates_idempotency_by_user_even_with_same_key_string(
    db_session: AsyncSession,
) -> None:
    """서로 다른 사용자가 우연히 같은 문자열의 Idempotency-Key를 써도, 멱등성 scope에는
    user_id가 포함되므로 서로의 응답을 재현하거나 충돌시키면 안 된다."""
    shared_key = "shared-idempotency-key-string-01"

    owner_a = await _create_user(db_session, email="isolation-a@example.com")
    medication_a = await _create_medication(db_session, user=owner_a)
    finalized_a = await _create_ready_search(db_session, medication=medication_a, user=owner_a)

    owner_b = await _create_user(db_session, email="isolation-b@example.com")
    medication_b = await _create_medication(db_session, user=owner_b)
    finalized_b = await _create_ready_search(db_session, medication=medication_b, user=owner_b)

    service = _service(db_session)
    result_a = await service.confirm_candidate(
        user=owner_a,
        request=ConfirmMedicationCandidateRequest(
            prescription_version_medication_id=medication_a.id,
            candidate_search_result_id=finalized_a.results[0].id,
        ),
        idempotency_key=shared_key,
    )
    result_b = await service.confirm_candidate(
        user=owner_b,
        request=ConfirmMedicationCandidateRequest(
            prescription_version_medication_id=medication_b.id,
            candidate_search_result_id=finalized_b.results[0].id,
        ),
        idempotency_key=shared_key,
    )

    assert result_a.identification_id != result_b.identification_id
    assert result_a.prescription_version_medication_id == medication_a.id
    assert result_b.prescription_version_medication_id == medication_b.id
    assert await _count_idempotency_records(db_session) == 2


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
        idempotency_key="a" * 32,
    )

    assert result.identification_event_id is not None
    assert result.prescription_version_medication_id == medication.id
    assert result.status.value == "UNRESOLVED"
    assert result.search_status.value == "INVALIDATED_USER_REJECTED"
    assert result.rejected_at is not None


async def test_reject_candidate_replays_stored_response_for_same_key_and_request(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="reject2@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)
    request = RejectMedicationCandidateRequest(
        search_id=finalized.search.id,
        candidate_search_result_id=finalized.results[0].id,
    )

    first = await service.reject_candidate(user=owner, request=request, idempotency_key="b" * 32)
    second = await service.reject_candidate(user=owner, request=request, idempotency_key="b" * 32)

    assert second.identification_event_id == first.identification_event_id
    assert second.rejected_at == first.rejected_at


async def test_reject_candidate_derives_parent_resource_id_from_search_result(
    db_session: AsyncSession,
) -> None:
    """거절 요청 body에는 prescription_version_medication_id가 없다 — service가 search_id·
    candidate_search_result_id로부터 그 값을 조회해 멱등성 scope로 써야 한다(#311)."""
    service = _service(db_session)
    owner = await _create_user(db_session, email="reject3@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)

    result = await service.reject_candidate(
        user=owner,
        request=RejectMedicationCandidateRequest(
            search_id=finalized.search.id,
            candidate_search_result_id=finalized.results[0].id,
        ),
        idempotency_key="c" * 32,
    )

    assert result.prescription_version_medication_id == medication.id


async def test_reject_candidate_rejects_search_id_not_matching_candidate_result(
    db_session: AsyncSession,
) -> None:
    service = _service(db_session)
    owner = await _create_user(db_session, email="reject4@example.com")
    medication = await _create_medication(db_session, user=owner)
    finalized = await _create_ready_search(db_session, medication=medication, user=owner)

    with pytest.raises(ApiError) as exc_info:
        await service.reject_candidate(
            user=owner,
            request=RejectMedicationCandidateRequest(
                search_id=uuid4(),
                candidate_search_result_id=finalized.results[0].id,
            ),
            idempotency_key="d" * 32,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "CANDIDATE_SEARCH_NOT_FOUND"
