from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_jobs import AiJobType
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.ocr_repository import OcrRepository
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


async def _create_document(session: AsyncSession) -> MedicalDocument:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"ocr-repo-{suffix}@example.com",
        hashed_password="hashed-password",
        name="OCR저장소테스터",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{int(suffix, 16) % 100000000:08d}",
    )
    session.add(user)
    await session.flush()
    profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
    session.add(profile)
    await session.flush()

    document = MedicalDocument(
        uploaded_by=user.id,
        profile_id=profile.id,
        original_file_name="prescription.jpg",
        object_key=f"test/{uuid4()}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    session.add(document)
    await session.flush()
    return document


async def test_get_by_ai_job_id_returns_matching_ocr_job(db_session: AsyncSession) -> None:
    """#212 영속 매핑: `ocr_job.ai_job_id`로 직접 조회할 수 있어야 rediscovery·
    `GET /jobs/{job_id}`가 Outbox 30일 보존과 무관하게 Job 90일 보존 동안 값을 찾을 수
    있습니다."""
    document = await _create_document(db_session)
    ai_job = await AsyncJobRepository(db_session).create_job(
        user_id=document.uploaded_by, job_type=AiJobType.OCR, prescription_version_id=None
    )
    job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING, ai_job_id=ai_job.id)
    db_session.add(job)
    await db_session.flush()

    found = await OcrRepository(db_session).get_by_ai_job_id(ai_job_id=ai_job.id)

    assert found is not None
    assert found.id == job.id


async def test_get_by_ai_job_id_returns_none_when_unset(db_session: AsyncSession) -> None:
    document = await _create_document(db_session)
    job = OcrJob(document_id=document.id, ocr_status=OcrStatus.PENDING)
    db_session.add(job)
    await db_session.flush()

    found = await OcrRepository(db_session).get_by_ai_job_id(ai_job_id=uuid4())

    assert found is None


@pytest.mark.asyncio
async def test_postgresql_generates_increasing_created_sequence(
    db_session: AsyncSession,
) -> None:
    """created_sequence를 생략하면 PostgreSQL이 증가값을 생성합니다."""
    document = await _create_document(db_session)

    first_job = OcrJob(
        document_id=document.id,
        ocr_status=OcrStatus.PENDING,
    )
    db_session.add(first_job)
    await db_session.flush()
    await db_session.refresh(
        first_job,
        attribute_names=["created_sequence"],
    )

    second_job = OcrJob(
        document_id=document.id,
        ocr_status=OcrStatus.PENDING,
    )
    db_session.add(second_job)
    await db_session.flush()
    await db_session.refresh(
        second_job,
        attribute_names=["created_sequence"],
    )

    assert first_job.created_sequence > 0
    assert second_job.created_sequence > first_job.created_sequence


@pytest.mark.asyncio
async def test_get_latest_completed_job_uses_created_sequence_when_created_at_is_same(
    db_session: AsyncSession,
) -> None:
    document = await _create_document(db_session)
    same_created_at = datetime(2026, 8, 24, 10, 0, 0, tzinfo=UTC)
    completed_at = datetime(2026, 8, 24, 10, 0, 1, tzinfo=UTC)

    older_job_id = UUID("00000000-0000-0000-0000-000000000001")
    newer_job_id = UUID("00000000-0000-0000-0000-000000000002")
    db_session.add_all(
        [
            OcrJob(
                id=older_job_id,
                document_id=document.id,
                ocr_status=OcrStatus.COMPLETED,
                created_sequence=1,
                created_at=same_created_at,
                completed_at=completed_at,
            ),
            OcrJob(
                id=newer_job_id,
                document_id=document.id,
                ocr_status=OcrStatus.COMPLETED,
                created_sequence=2,
                created_at=same_created_at,
                completed_at=completed_at,
            ),
        ]
    )
    await db_session.flush()

    latest_job = await OcrRepository(db_session).get_latest_completed_job(document=document)

    assert latest_job is not None
    assert latest_job.id == newer_job_id
