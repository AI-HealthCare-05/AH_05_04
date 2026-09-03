from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.async_jobs import _FAILURE_CODE_VALUES, AiJobStatus, AiJobType, DomainType
from app.models.guides import Guide, GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.prescriptions import Prescription
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.ocr_repository import OcrRepository
from app.services.job_intake import DomainReference, JobIntakeService
from app.services.job_status import _FAILURE_MESSAGES, JobStatusService
from app.tests.conftest import test_engine


def test_failure_messages_cover_every_allowed_failure_code() -> None:
    """`_FAILURE_MESSAGES`의 key 집합이 `_FAILURE_CODE_VALUES`와 어긋나면, 새로 추가된
    failure_code가 조용히 `INTERNAL_ERROR` fallback 메시지로 가려질 수 있으므로 고정합니다."""
    assert set(_FAILURE_MESSAGES) == set(_FAILURE_CODE_VALUES)


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


def _service(session: AsyncSession) -> JobStatusService:
    return JobStatusService(
        job_repository=AsyncJobRepository(session),
        ocr_repository=OcrRepository(session),
        guide_repository=GuideRepository(session),
        chat_repository=ChatRepository(session),
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


async def _create_document(session: AsyncSession, *, user: User) -> MedicalDocument:
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
    return document


async def _accept_ocr_job(session: AsyncSession, *, user: User, document: MedicalDocument) -> UUID:
    """실제 accept_job() 경로로 ai_job + outbox_event(domain 참조 포함) + ocr_job을 만듭니다."""

    async def create_domain_placeholder(_job_id: UUID) -> DomainReference:
        ocr_job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING)
        session.add(ocr_job)
        await session.flush()
        return DomainReference(domain_type=DomainType.OCR_JOB, domain_id=ocr_job.id)

    service = JobIntakeService(AsyncJobRepository(session))
    result = await service.accept_job(
        user_id=user.id,
        job_type=AiJobType.OCR,
        operation_id="ocr.create_job",
        idempotency_key=f"test-job-status-{uuid4().hex[:20]}",
        fingerprint={"job_type": "OCR", "document_id": str(document.id)},
        create_domain_placeholder=create_domain_placeholder,
        trace_id="a" * 32,
    )
    return result.job.id


async def _create_confirmed_prescription(
    session: AsyncSession, *, user: User, document: MedicalDocument
) -> Prescription:
    profile = await session.scalar(
        select(Profile).where(Profile.user_id == user.id, Profile.profile_type == ProfileType.SELF)
    )
    assert profile is not None
    ocr_job = OcrJob(document_id=document.id, ocr_status=OcrStatus.COMPLETED, completed_at=datetime.now(UTC))
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
    return prescription


async def _accept_guide_job(session: AsyncSession, *, user: User, prescription: Prescription) -> tuple[UUID, UUID]:
    """Guide 접수는 아직 `accept_job()`에 연결되지 않아(팀 결정, #148) repository를 직접 조합합니다.
    반환값은 (job_id, guide_id)."""
    guide = Guide(
        prescription_id=prescription.id,
        profile_id=prescription.profile_id,
        generation_status=GuideGenerationStatus.GENERATING,
    )
    session.add(guide)
    await session.flush()

    repo = AsyncJobRepository(session)
    job = await repo.create_job(user_id=user.id, job_type=AiJobType.GUIDE, prescription_version_id=None)
    await repo.create_outbox_event(job=job, trace_id="a" * 32, domain_type=DomainType.GUIDE, domain_id=guide.id)
    return job.id, guide.id


async def test_get_job_status_returns_data_for_owner_pending_job(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email=f"js-a-{uuid4().hex[:12]}@test.local")
    document = await _create_document(db_session, user=user)
    job_id = await _accept_ocr_job(db_session, user=user, document=document)

    result = await _service(db_session).get_job_status(user=user, job_id=job_id)

    assert result.data.job_id == job_id
    assert result.data.job_type == AiJobType.OCR
    assert result.data.status == AiJobStatus.PENDING
    assert result.data.domain_type == DomainType.OCR_JOB
    assert result.data.status_url == f"/api/v1/jobs/{job_id}"
    assert result.data.result_url is None
    assert result.retry_after_seconds is None
    assert result.data.error is None


async def test_get_job_status_rejects_other_users_job(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email=f"js-owner-{uuid4().hex[:10]}@test.local")
    other = await _create_user(db_session, email=f"js-other-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=owner)
    job_id = await _accept_ocr_job(db_session, user=owner, document=document)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).get_job_status(user=other, job_id=job_id)
    assert exc_info.value.status_code == 404


async def test_get_job_status_rejects_when_domain_result_profile_mismatch(db_session: AsyncSession) -> None:
    """§6: ai_job.user_id는 일치해도, 도메인 결과(ocr_job → medical_document.profile_id)가
    다른 사용자 소유면 fail-closed 404여야 합니다. Job과 도메인 결과의 소유자가 갈리는 상황을
    직접 만들기 위해 accept_job() 대신 repository를 낮은 수준에서 조합합니다.
    """
    job_owner = await _create_user(db_session, email=f"js-jown-{uuid4().hex[:10]}@test.local")
    document_owner = await _create_user(db_session, email=f"js-down-{uuid4().hex[:10]}@test.local")
    other_document = await _create_document(db_session, user=document_owner)
    ocr_job = OcrJob(document_id=other_document.id, ocr_status=OcrStatus.PENDING)
    db_session.add(ocr_job)
    await db_session.flush()

    repo = AsyncJobRepository(db_session)
    job = await repo.create_job(user_id=job_owner.id, job_type=AiJobType.OCR, prescription_version_id=None)
    await repo.create_outbox_event(job=job, trace_id="a" * 32, domain_type=DomainType.OCR_JOB, domain_id=ocr_job.id)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).get_job_status(user=job_owner, job_id=job.id)
    assert exc_info.value.status_code == 404


async def test_get_job_status_returns_result_url_only_when_completed(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email=f"js-comp-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    job_id = await _accept_ocr_job(db_session, user=user, document=document)

    repo = AsyncJobRepository(db_session)
    job = await repo.get_job(job_id=job_id)
    assert job is not None
    job.status = AiJobStatus.COMPLETED
    job.completed_at = datetime.now(UTC)
    await db_session.flush()

    result = await _service(db_session).get_job_status(user=user, job_id=job_id)

    domain_id = result.data.domain_id
    assert result.data.result_url == f"/api/v1/ocr-jobs/{domain_id}"


async def test_get_job_status_sets_retry_after_seconds_when_retry_wait(db_session: AsyncSession) -> None:
    """outbox-stream-v1.md §소비와 fencing: `RETRY_WAIT` 전환 직후~Reconciler가 다음 attempt
    Outbox를 만들기 전에는 fencing을 지키기 위해 `job.expected_event_id`가 `NULL`입니다.
    실제 Reconciler가 이 값을 비우는 것과 같은 상태를 재현해, 그 gap에서도 `404`가 되지
    않는지 검증합니다."""
    user = await _create_user(db_session, email=f"js-retry-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    job_id = await _accept_ocr_job(db_session, user=user, document=document)

    repo = AsyncJobRepository(db_session)
    job = await repo.get_job(job_id=job_id)
    assert job is not None
    job.status = AiJobStatus.RETRY_WAIT
    job.available_at = datetime.now(UTC) + timedelta(seconds=30)
    # 실제 전이처럼 last_consumed_event_id를 채운 뒤 expected_event_id를 비웁니다.
    job.last_consumed_event_id = job.expected_event_id
    job.expected_event_id = None
    await db_session.flush()

    result = await _service(db_session).get_job_status(user=user, job_id=job_id)

    assert result.data.status == AiJobStatus.RETRY_WAIT
    assert result.retry_after_seconds is not None
    assert 0 < result.retry_after_seconds <= 30
    assert result.data.result_url is None


async def test_get_job_status_floors_retry_after_seconds_once_available_at_has_passed(
    db_session: AsyncSession,
) -> None:
    """`available_at`이 지나도 Job은 바로 `PROCESSING`이 되지 않고, Reconciler 주기 → 새
    Outbox 생성 → Publisher `XADD`(#219) → Worker lease 획득까지는 `RETRY_WAIT`가 유지됩니다.
    그 구간에 `Retry-After: 0`을 보내면 client가 대기 없이 재조회를 반복하므로, 0이 아니라
    최소 1초 하한을 반환해야 합니다."""
    user = await _create_user(db_session, email=f"js-retry2-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    job_id = await _accept_ocr_job(db_session, user=user, document=document)

    repo = AsyncJobRepository(db_session)
    job = await repo.get_job(job_id=job_id)
    assert job is not None
    job.status = AiJobStatus.RETRY_WAIT
    job.available_at = datetime.now(UTC) - timedelta(seconds=5)
    # 실제 전이처럼 last_consumed_event_id를 채운 뒤 expected_event_id를 비웁니다.
    job.last_consumed_event_id = job.expected_event_id
    job.expected_event_id = None
    await db_session.flush()

    result = await _service(db_session).get_job_status(user=user, job_id=job_id)

    assert result.data.status == AiJobStatus.RETRY_WAIT
    assert result.retry_after_seconds == 1


async def test_get_job_status_returns_safe_error_for_failed_job(db_session: AsyncSession) -> None:
    """`FAILED`로 종결된 뒤에는 `job.expected_event_id`가 다시 채워지지 않고 `NULL`로 남으므로,
    그 상태를 재현해 `404`가 되지 않는지 검증합니다."""
    user = await _create_user(db_session, email=f"js-fail-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    job_id = await _accept_ocr_job(db_session, user=user, document=document)

    repo = AsyncJobRepository(db_session)
    job = await repo.get_job(job_id=job_id)
    assert job is not None
    job.status = AiJobStatus.FAILED
    job.completed_at = datetime.now(UTC)
    job.failure_code = "TIMEOUT"
    job.failure_detail = "내부 원문 오류 — 외부에 노출되면 안 됨"
    # 실제 전이처럼 last_consumed_event_id를 채운 뒤 expected_event_id를 비웁니다.
    job.last_consumed_event_id = job.expected_event_id
    job.expected_event_id = None
    await db_session.flush()

    result = await _service(db_session).get_job_status(user=user, job_id=job_id)

    assert result.data.error is not None
    assert result.data.error.code == "TIMEOUT"
    assert "내부 원문 오류" not in result.data.error.message


async def test_get_job_status_uses_persistent_ai_job_id_when_outbox_event_is_purged(
    db_session: AsyncSession,
) -> None:
    """#212: `ocr_job.ai_job_id`가 채워져 있으면, Outbox row가 30일 보존 후 이미 삭제된
    상태(여기서는 애초에 만들지 않아 재현)에서도 영속 매핑으로 값을 찾아야 합니다 —
    `get_interim_domain_reference()`(Outbox 기반)만 쓰면 이 경우 `404`가 됩니다."""
    user = await _create_user(db_session, email=f"js-persist-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)

    repo = AsyncJobRepository(db_session)
    job = await repo.create_job(user_id=user.id, job_type=AiJobType.OCR, prescription_version_id=None)
    ocr_job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING, ai_job_id=job.id)
    db_session.add(ocr_job)
    await db_session.flush()

    result = await _service(db_session).get_job_status(user=user, job_id=job.id)

    assert result.data.domain_type == DomainType.OCR_JOB
    assert result.data.domain_id == ocr_job.id


async def test_rediscover_ocr_job_uses_persistent_ai_job_id_when_outbox_event_is_purged(
    db_session: AsyncSession,
) -> None:
    """`rediscover_ocr_job`도 같은 이유로 `ocr_job.ai_job_id`를 Outbox 역조회보다 먼저
    확인해야 합니다."""
    user = await _create_user(db_session, email=f"js-redi-persist-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)

    repo = AsyncJobRepository(db_session)
    job = await repo.create_job(user_id=user.id, job_type=AiJobType.OCR, prescription_version_id=None)
    ocr_job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING, ai_job_id=job.id)
    db_session.add(ocr_job)
    await db_session.flush()

    result = await _service(db_session).rediscover_ocr_job(user=user, document_id=document.id)

    assert result.data.job_id == job.id


async def test_rediscover_ocr_job_returns_latest_job_status(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email=f"js-redi-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    job_id = await _accept_ocr_job(db_session, user=user, document=document)

    result = await _service(db_session).rediscover_ocr_job(user=user, document_id=document.id)

    assert result.data.job_id == job_id
    assert result.data.domain_type == DomainType.OCR_JOB


async def test_rediscover_ocr_job_returns_most_recent_when_multiple(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email=f"js-redi2-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    await _accept_ocr_job(db_session, user=user, document=document)
    latest_job_id = await _accept_ocr_job(db_session, user=user, document=document)

    result = await _service(db_session).rediscover_ocr_job(user=user, document_id=document.id)

    assert result.data.job_id == latest_job_id


async def test_rediscover_ocr_job_raises_404_when_no_job_exists(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email=f"js-redi3-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).rediscover_ocr_job(user=user, document_id=document.id)
    assert exc_info.value.status_code == 404


async def test_rediscover_ocr_job_rejects_other_users_document(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email=f"js-redi4-{uuid4().hex[:10]}@test.local")
    other = await _create_user(db_session, email=f"js-redi5-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=owner)
    await _accept_ocr_job(db_session, user=owner, document=document)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).rediscover_ocr_job(user=other, document_id=document.id)
    assert exc_info.value.status_code == 404


async def test_rediscover_guide_job_returns_latest_job_status(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email=f"js-redig-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    prescription = await _create_confirmed_prescription(db_session, user=user, document=document)
    job_id, _ = await _accept_guide_job(db_session, user=user, prescription=prescription)

    result = await _service(db_session).rediscover_guide_job(user=user, prescription_id=prescription.id)

    assert result.data.job_id == job_id
    assert result.data.domain_type == DomainType.GUIDE


async def test_rediscover_guide_job_raises_404_when_no_guide_exists(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, email=f"js-redig2-{uuid4().hex[:10]}@test.local")
    document = await _create_document(db_session, user=user)
    prescription = await _create_confirmed_prescription(db_session, user=user, document=document)

    with pytest.raises(ApiError) as exc_info:
        await _service(db_session).rediscover_guide_job(user=user, prescription_id=prescription.id)
    assert exc_info.value.status_code == 404
