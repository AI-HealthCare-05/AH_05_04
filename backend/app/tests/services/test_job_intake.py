import asyncio
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.utils.idempotency import IdempotencyKeyFormatError
from app.models.async_jobs import AiJob, AiJobType, IdempotencyRecord, OutboxEvent, OutboxEventKind
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.services.job_intake import (
    CreateDomainPlaceholder,
    IdempotencyKeyConflictError,
    JobIntakeResult,
    JobIntakeService,
)
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


def _ocr_placeholder_factory(
    session: AsyncSession, *, document_id: UUID, user: User
) -> tuple[list[OcrJob], list[UUID], CreateDomainPlaceholder]:
    """OCR 도메인 placeholder 콜백 — 실제 OCR router/service(#148)가 연결할 형태를 흉내냅니다.

    `JobIntakeService`는 도메인 비의존적이라 `profile_id` 소유권을 알지 못합니다 — async-job-v1.md가
    소유권 확인을 요구하는 곳은 Job 조회/결과 조회 경로뿐이고, `ai_job`도 `profile_id`를 직접 저장하지
    않습니다(track-a-migration-rollback-v1.md §4.3). 따라서 소유권 검증은 도메인 리소스를 아는 이
    콜백(호출자) 책임이며, `ocr.py`의 `execute_ocr`와 동일하게 `get_owned()` 실패 시 404로 fail-closed
    처리합니다.

    `ocr_job.ai_job_id` 컬럼은 #212(OCR mapping 기준 governance decision) 확정 전까지 존재하지
    않으므로, 콜백이 실제로 받은 `job_id`는 별도 리스트로 기록해 검증합니다.
    """
    created: list[OcrJob] = []
    received_job_ids: list[UUID] = []
    document_repo = MedicalDocumentRepository(session)

    async def create_domain_placeholder(job_id: UUID) -> None:
        document = await document_repo.get_owned(document_id=document_id, user=user)
        if document is None:
            raise ApiError(
                status_code=404,
                code="MEDICAL_DOCUMENT_NOT_FOUND",
                message="의료문서를 찾을 수 없습니다.",
            )
        ocr_job = OcrJob(document_id=document_id, ocr_status=OcrStatus.PENDING)
        session.add(ocr_job)
        await session.flush()
        created.append(ocr_job)
        received_job_ids.append(job_id)

    return created, received_job_ids, create_domain_placeholder


@pytest.mark.asyncio
async def test_accept_job_creates_placeholder_job_outbox_and_idempotency_record(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"intake-{uuid4().hex[:12]}@test.local")
    document = await _create_document(db_session, user=user)
    service = JobIntakeService(AsyncJobRepository(db_session))
    created_ocr_jobs, received_job_ids, create_placeholder = _ocr_placeholder_factory(
        db_session, document_id=document.id, user=user
    )

    result = await service.accept_job(
        user_id=user.id,
        job_type=AiJobType.OCR,
        operation_id="ocr.create_job",
        idempotency_key="test-idempotency-key-0001",
        fingerprint={"job_type": "OCR", "document_id": str(document.id)},
        create_domain_placeholder=create_placeholder,
    )

    assert result.is_duplicate is False
    assert result.job.job_type == AiJobType.OCR
    assert result.job.user_id == user.id
    assert result.job.max_attempts == 3  # async-job-v1.md 기본값: OCR 3

    assert len(created_ocr_jobs) == 1
    assert received_job_ids == [result.job.id]

    outbox_event = await db_session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == result.job.id))
    assert outbox_event is not None
    assert outbox_event.event_kind == OutboxEventKind.JOB_EXECUTE
    assert outbox_event.attempt == 1

    await db_session.refresh(result.job)
    assert result.job.expected_event_id == outbox_event.event_id

    record = await db_session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.job_id == result.job.id))
    assert record is not None
    assert record.parent_resource_id is None


@pytest.mark.asyncio
async def test_accept_job_same_key_same_fingerprint_returns_existing_job(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"intake-{uuid4().hex[:12]}@test.local")
    document = await _create_document(db_session, user=user)
    service = JobIntakeService(AsyncJobRepository(db_session))
    idempotency_key = "test-idempotency-key-0002"
    fingerprint = {"job_type": "OCR", "document_id": str(document.id)}

    created_ocr_jobs, _, create_placeholder = _ocr_placeholder_factory(db_session, document_id=document.id, user=user)
    first = await service.accept_job(
        user_id=user.id,
        job_type=AiJobType.OCR,
        operation_id="ocr.create_job",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        create_domain_placeholder=create_placeholder,
    )

    async def unexpected_placeholder(_job_id: UUID) -> None:
        raise AssertionError("중복 요청에서는 새 도메인 placeholder를 만들면 안 됩니다.")

    second = await service.accept_job(
        user_id=user.id,
        job_type=AiJobType.OCR,
        operation_id="ocr.create_job",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        create_domain_placeholder=unexpected_placeholder,
    )

    assert second.is_duplicate is True
    assert second.job.id == first.job.id
    assert len(created_ocr_jobs) == 1

    job_count = await db_session.scalar(select(AiJob).where(AiJob.user_id == user.id))
    assert job_count is not None  # 정상적으로 하나만 존재


@pytest.mark.asyncio
async def test_accept_job_same_key_different_fingerprint_raises_conflict(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"intake-{uuid4().hex[:12]}@test.local")
    document = await _create_document(db_session, user=user)
    service = JobIntakeService(AsyncJobRepository(db_session))
    idempotency_key = "test-idempotency-key-0003"

    _, _, create_placeholder = _ocr_placeholder_factory(db_session, document_id=document.id, user=user)
    await service.accept_job(
        user_id=user.id,
        job_type=AiJobType.OCR,
        operation_id="ocr.create_job",
        idempotency_key=idempotency_key,
        fingerprint={"job_type": "OCR", "document_id": str(document.id)},
        create_domain_placeholder=create_placeholder,
    )

    async def unexpected_placeholder(_job_id: UUID) -> None:
        raise AssertionError("충돌 요청에서는 새 도메인 placeholder를 만들면 안 됩니다.")

    with pytest.raises(IdempotencyKeyConflictError):
        await service.accept_job(
            user_id=user.id,
            job_type=AiJobType.OCR,
            operation_id="ocr.create_job",
            idempotency_key=idempotency_key,
            fingerprint={"job_type": "OCR", "document_id": str(uuid4())},
            create_domain_placeholder=unexpected_placeholder,
        )


@pytest.mark.asyncio
async def test_accept_job_rejects_invalid_idempotency_key_format(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email=f"intake-{uuid4().hex[:12]}@test.local")
    document = await _create_document(db_session, user=user)
    service = JobIntakeService(AsyncJobRepository(db_session))

    async def unexpected_placeholder(_job_id: UUID) -> None:
        raise AssertionError("형식이 잘못된 key는 placeholder를 만들기 전에 거부돼야 합니다.")

    with pytest.raises(IdempotencyKeyFormatError):
        await service.accept_job(
            user_id=user.id,
            job_type=AiJobType.OCR,
            operation_id="ocr.create_job",
            idempotency_key="too-short",
            fingerprint={"job_type": "OCR", "document_id": str(document.id)},
            create_domain_placeholder=unexpected_placeholder,
        )


@pytest.mark.asyncio
async def test_accept_job_rolls_back_all_records_when_document_not_owned_by_user(
    db_session: AsyncSession,
) -> None:
    """`JobIntakeService`는 도메인 비의존적이라 profile_id 소유권을 직접 검증하지 않으므로,
    실제 소유권 검증은 `create_domain_placeholder` 콜백(#148이 연결할 자리)의 책임입니다.
    콜백이 소유권 실패로 `ApiError`를 던지면 같은 transaction 안의 AI_JOB·OUTBOX_EVENT·
    IDEMPOTENCY_RECORD도 함께 롤백되어야 합니다.
    """
    owner = await _create_user(db_session, email=f"intake-owner-{uuid4().hex[:12]}@test.local")
    document = await _create_document(db_session, user=owner)

    other_user = await _create_user(db_session, email=f"intake-other-{uuid4().hex[:12]}@test.local")
    service = JobIntakeService(AsyncJobRepository(db_session))
    _, _, create_placeholder = _ocr_placeholder_factory(db_session, document_id=document.id, user=other_user)

    with pytest.raises(ApiError) as exc_info:
        await service.accept_job(
            user_id=other_user.id,
            job_type=AiJobType.OCR,
            operation_id="ocr.create_job",
            idempotency_key="test-idempotency-key-0004",
            fingerprint={"job_type": "OCR", "document_id": str(document.id)},
            create_domain_placeholder=create_placeholder,
        )
    assert exc_info.value.code == "MEDICAL_DOCUMENT_NOT_FOUND"

    job_count = await db_session.scalar(select(AiJob).where(AiJob.user_id == other_user.id))
    assert job_count is None
    record_count = await db_session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == other_user.id))
    assert record_count is None
    outbox_count = await db_session.scalar(
        select(OutboxEvent).join(AiJob, OutboxEvent.job_id == AiJob.id).where(AiJob.user_id == other_user.id)
    )
    assert outbox_count is None


@pytest.mark.asyncio
async def test_accept_job_concurrent_same_key_creates_only_one_job() -> None:
    """idempotency-v1.md: 동시 최초 요청은 DB unique constraint로 하나만 승리시키고,
    패자는 저장된 지문을 비교해 기존 Job을 반환해야 합니다. savepoint 격리로는 실제 동시성을
    재현할 수 없으므로, 이 테스트만 별도 세션 2개로 test DB에 직접 commit합니다.
    """
    idempotency_key = "test-idempotency-key-concurrent-0001"
    setup_session = AsyncSession(bind=test_engine, expire_on_commit=False)
    try:
        user = await _create_user(setup_session, email=f"intake-race-{uuid4().hex[:12]}@test.local")
        document = await _create_document(setup_session, user=user)
        await setup_session.commit()
    finally:
        await setup_session.close()

    async def accept() -> JobIntakeResult:
        session = AsyncSession(bind=test_engine, expire_on_commit=False)
        try:
            service = JobIntakeService(AsyncJobRepository(session))

            async def create_placeholder(_job_id: UUID) -> None:
                session.add(OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING))
                await session.flush()

            result = await service.accept_job(
                user_id=user.id,
                job_type=AiJobType.OCR,
                operation_id="ocr.create_job",
                idempotency_key=idempotency_key,
                fingerprint={"job_type": "OCR", "document_id": str(document.id)},
                create_domain_placeholder=create_placeholder,
            )
            await session.commit()
            return result
        finally:
            await session.close()

    try:
        first, second = await asyncio.gather(accept(), accept())

        assert first.job.id == second.job.id
        assert {first.is_duplicate, second.is_duplicate} == {False, True}

        verify_session = AsyncSession(bind=test_engine, expire_on_commit=False)
        try:
            jobs = (await verify_session.scalars(select(AiJob).where(AiJob.user_id == user.id))).all()
            outbox_events = (
                await verify_session.scalars(select(OutboxEvent).where(OutboxEvent.job_id == first.job.id))
            ).all()
            ocr_jobs = (await verify_session.scalars(select(OcrJob).where(OcrJob.document_id == document.id))).all()
            assert len(jobs) == 1
            assert len(outbox_events) == 1
            assert len(ocr_jobs) == 1
        finally:
            await verify_session.close()
    finally:
        cleanup_session = AsyncSession(bind=test_engine, expire_on_commit=False)
        try:
            await cleanup_session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.user_id == user.id))
            await cleanup_session.execute(
                delete(OutboxEvent).where(OutboxEvent.job_id.in_(select(AiJob.id).where(AiJob.user_id == user.id)))
            )
            await cleanup_session.execute(delete(OcrJob).where(OcrJob.document_id == document.id))
            await cleanup_session.execute(delete(AiJob).where(AiJob.user_id == user.id))
            await cleanup_session.execute(delete(MedicalDocument).where(MedicalDocument.id == document.id))
            await cleanup_session.execute(delete(Profile).where(Profile.user_id == user.id))
            await cleanup_session.execute(delete(User).where(User.id == user.id))
            await cleanup_session.commit()
        finally:
            await cleanup_session.close()
