import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.users import Gender, User
from app.tests.conftest import test_engine


def pytest_collection_modifyitems(config: pytest.Config) -> None:
    numprocesses = getattr(config.option, "numprocesses", None)
    if os.environ.get("PYTEST_XDIST_WORKER") or numprocesses not in (None, 0, "0"):
        raise pytest.UsageError(
            # 이 테스트는 커밋된 PostgreSQL fixture를 공유하므로 병렬 실행하면 안 됩니다.
            "app/tests/chat_integration commits shared PostgreSQL fixtures "
            "and must run in a serial pytest job without xdist"
        )


@pytest_asyncio.fixture(autouse=True)
async def isolate_database() -> AsyncIterator[None]:
    """Replace the parent savepoint fixture; these tests require committed rows and independent connections."""

    yield


@dataclass(frozen=True)
class CommittedChatFixture:
    user: User
    document_id: UUID
    ocr_job_id: UUID
    prescription_id: UUID
    medication_id: UUID
    session_ids: tuple[UUID, UUID]


@pytest_asyncio.fixture
async def committed_chat_fixture() -> AsyncIterator[CommittedChatFixture]:
    token = uuid4().hex
    fixture: CommittedChatFixture | None = None
    try:
        async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as session:
            # 동시성 검증이 PostgreSQL 17 테스트 DB에서 실행되는지 확인합니다.
            postgresql_version = str(await session.scalar(text("SELECT current_setting('server_version')")))
            assert postgresql_version.startswith("17."), f"PostgreSQL 17 required, got {postgresql_version}"
            user = User(
                email=f"cc-{token[:12]}@example.com",
                hashed_password="synthetic-hash",
                name="합성 동시성",
                gender=Gender.FEMALE,
                birthday=date(1990, 1, 1),
                phone_number=f"010{uuid4().int % 100_000_000:08d}",
            )
            session.add(user)
            await session.flush()
            document = MedicalDocument(
                user_id=user.id,
                original_file_name=f"synthetic-{token}.jpg",
                object_key=f"synthetic/chat-concurrency/{token}.jpg",
                file_mime_type="image/jpeg",
                file_size_bytes=128,
            )
            session.add(document)
            await session.flush()
            ocr_job = OcrJob(document_id=document.id)
            session.add(ocr_job)
            await session.flush()
            prescription = Prescription(
                document_id=document.id,
                source_ocr_job_id=ocr_job.id,
                prescribed_date=date(2026, 8, 21),
                confirmed_at=datetime.now(UTC),
            )
            session.add(prescription)
            await session.flush()
            medication = Medication(
                prescription_id=prescription.id,
                medication_name="동시성 검증용 합성약",
                dose_value=Decimal("0.125"),
                dose_unit="mg",
                frequency_per_day=2,
                timing_text="식후",
                duration_days=7,
                display_order=1,
            )
            first_session = ChatSession(prescription_id=prescription.id)
            second_session = ChatSession(prescription_id=prescription.id)
            session.add_all([medication, first_session, second_session])
            await session.flush()
            fixture = CommittedChatFixture(
                user=user,
                document_id=document.id,
                ocr_job_id=ocr_job.id,
                prescription_id=prescription.id,
                medication_id=medication.id,
                session_ids=(first_session.id, second_session.id),
            )
            await session.commit()

        yield fixture
    finally:
        if fixture is not None:
            async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as cleanup:
                await cleanup.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(fixture.session_ids)))
                await cleanup.execute(delete(ChatSession).where(ChatSession.id.in_(fixture.session_ids)))
                await cleanup.execute(delete(Medication).where(Medication.id == fixture.medication_id))
                await cleanup.execute(delete(Prescription).where(Prescription.id == fixture.prescription_id))
                await cleanup.execute(delete(OcrJob).where(OcrJob.id == fixture.ocr_job_id))
                await cleanup.execute(delete(MedicalDocument).where(MedicalDocument.id == fixture.document_id))
                await cleanup.execute(delete(User).where(User.id == fixture.user.id))
                await cleanup.commit()

                assert (
                    await cleanup.scalar(select(ChatMessage.id).where(ChatMessage.session_id.in_(fixture.session_ids)))
                    is None
                )
                assert (
                    await cleanup.scalar(select(ChatSession.id).where(ChatSession.id.in_(fixture.session_ids))) is None
                )
                assert await cleanup.get(Medication, fixture.medication_id) is None
                assert await cleanup.get(Prescription, fixture.prescription_id) is None
                assert await cleanup.get(OcrJob, fixture.ocr_job_id) is None
                assert await cleanup.get(MedicalDocument, fixture.document_id) is None
                assert await cleanup.get(User, fixture.user.id) is None
