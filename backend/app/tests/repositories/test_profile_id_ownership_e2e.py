"""medical_document -> prescription -> guide -> chat_session까지 profile_id가
실제 repository 진입점(create 계열)을 통해 전파되고, 각 repository의 소유권 조회가
동일 사용자에게는 성공하는 end-to-end happy path를 검증합니다.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.chat_repository import ChatRepository
from app.repositories.guide_repository import GuideRepository
from app.repositories.medical_document_repository import MedicalDocumentRepository
from app.repositories.ocr_repository import OcrRepository
from app.repositories.prescription_repository import PrescriptionRepository
from app.repositories.profile_ownership import get_self_profile_id
from app.repositories.user_repository import UserRepository
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


async def test_profile_id_propagates_end_to_end_and_owner_can_retrieve_every_resource(
    db_session: AsyncSession,
) -> None:
    token = uuid4().hex

    user = await UserRepository(db_session).create_user(
        email=f"profile-e2e-{token[:12]}@example.com",
        hashed_password="hashed-password",
        name="합성 사용자",
    )
    self_profile_id = await get_self_profile_id(db_session, user_id=user.id)
    assert self_profile_id is not None

    document = await MedicalDocumentRepository(db_session).create(
        user=user,
        original_file_name="prescription.jpg",
        object_key=f"profile-e2e/{token}.jpg",
        file_mime_type="image/jpeg",
        file_size_bytes=100,
    )
    assert document.uploaded_by == user.id
    assert document.profile_id == self_profile_id

    ocr_job = await OcrRepository(db_session).create_job(document=document)

    prescription = await PrescriptionRepository(db_session).create_with_medications(
        document=document,
        source_ocr_job=ocr_job,
        prescribed_date=date(2026, 8, 29),
        confirmed_at=datetime.now(UTC),
        medications=[{"medication_name": "타이레놀", "display_order": 1}],
    )
    assert prescription.profile_id == self_profile_id

    guide = await GuideRepository(db_session).create(prescription=prescription)
    assert guide.profile_id == self_profile_id

    chat_session = await ChatRepository(db_session).create_session(prescription=prescription)
    assert chat_session.profile_id == self_profile_id

    assert await MedicalDocumentRepository(db_session).get_owned(document_id=document.id, user=user) is not None
    assert (
        await OcrRepository(db_session).get_job_owned(job_id=ocr_job.id, user_id=user.id) is not None
    )
    assert (
        await PrescriptionRepository(db_session).get_owned(prescription_id=prescription.id, user_id=user.id)
        is not None
    )
    assert await GuideRepository(db_session).get_owned(guide_id=guide.id, user_id=user.id) is not None
    assert (
        await ChatRepository(db_session).get_session_owned(session_id=chat_session.id, user_id=user.id) is not None
    )
