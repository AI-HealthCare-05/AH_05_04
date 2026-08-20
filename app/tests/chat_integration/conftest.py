from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.users import Gender, User
from app.tests.conftest import test_engine


@pytest_asyncio.fixture(autouse=True)
async def isolate_database() -> AsyncIterator[None]:
    yield


@dataclass(frozen=True)
class CommittedChatFixture:
    user: User
    document_id: UUID
    ocr_job_id: UUID
    prescription_id: UUID
    session_ids: tuple[UUID, UUID]


@pytest_asyncio.fixture
async def committed_chat_fixture() -> AsyncIterator[CommittedChatFixture]:
    async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as session:
        user = User(
            email="chat-concurrency@example.com",
            hashed_password="synthetic-hash",
            name="합성 사용자",
            gender=Gender.FEMALE,
            birthday=date(1990, 1, 1),
            phone_number="01000009999",
        )
        session.add(user)
        await session.flush()
        document = MedicalDocument(
            user_id=user.id,
            original_file_name="synthetic-prescription.jpg",
            object_key="synthetic/prescription.jpg",
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
            prescribed_date=date(2026, 1, 1),
            confirmed_at=datetime.now(UTC),
        )
        session.add(prescription)
        await session.flush()
        session.add(
            Medication(
                prescription_id=prescription.id,
                medication_name="합성약",
                display_order=1,
                duration_days=7,
            )
        )
        first = ChatSession(prescription_id=prescription.id)
        second = ChatSession(prescription_id=prescription.id)
        session.add_all([first, second])
        await session.commit()
        fixture = CommittedChatFixture(
            user=user,
            document_id=document.id,
            ocr_job_id=ocr_job.id,
            prescription_id=prescription.id,
            session_ids=(first.id, second.id),
        )

    yield fixture

    async with AsyncSession(bind=test_engine, expire_on_commit=False) as cleanup:
        await cleanup.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(fixture.session_ids)))
        await cleanup.execute(delete(ChatSession).where(ChatSession.id.in_(fixture.session_ids)))
        await cleanup.execute(delete(Medication).where(Medication.prescription_id == fixture.prescription_id))
        await cleanup.execute(delete(Prescription).where(Prescription.id == fixture.prescription_id))
        await cleanup.execute(delete(OcrJob).where(OcrJob.id == fixture.ocr_job_id))
        await cleanup.execute(delete(MedicalDocument).where(MedicalDocument.id == fixture.document_id))
        await cleanup.execute(delete(User).where(User.id == fixture.user.id))
        await cleanup.commit()
        assert (
            await cleanup.scalar(select(ChatMessage.id).where(ChatMessage.session_id.in_(fixture.session_ids))) is None
        )
        assert await cleanup.scalar(select(ChatSession.id).where(ChatSession.id.in_(fixture.session_ids))) is None
        assert (
            await cleanup.scalar(select(Medication.id).where(Medication.prescription_id == fixture.prescription_id))
            is None
        )
        assert await cleanup.scalar(select(Prescription.id).where(Prescription.id == fixture.prescription_id)) is None
        assert await cleanup.scalar(select(OcrJob.id).where(OcrJob.id == fixture.ocr_job_id)) is None
        assert await cleanup.scalar(select(MedicalDocument.id).where(MedicalDocument.id == fixture.document_id)) is None
        assert await cleanup.scalar(select(User.id).where(User.id == fixture.user.id)) is None
