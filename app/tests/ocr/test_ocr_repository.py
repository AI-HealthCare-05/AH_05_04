from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob, OcrStatus
from app.models.users import Gender, User
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

    document = MedicalDocument(
        user_id=user.id,
        original_file_name="prescription.jpg",
        object_key=f"test/{uuid4()}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    session.add(document)
    await session.flush()
    return document


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
