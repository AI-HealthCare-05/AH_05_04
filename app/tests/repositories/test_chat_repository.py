from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatSession
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.users import Gender, User
from app.repositories.chat_repository import ChatRepository
from app.repositories.prescription_repository import PrescriptionRepository
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


async def _create_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email,
        hashed_password="hashed-password",
        name="합성 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    return user


async def _create_confirmed_prescription(session: AsyncSession, *, user: User) -> Prescription:
    document = MedicalDocument(
        user_id=user.id,
        original_file_name="synthetic-prescription.jpg",
        object_key=f"synthetic/{uuid4()}.jpg",
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
        prescribed_date=date(2026, 8, 20),
        confirmed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    session.add(prescription)
    await session.flush()
    return prescription


async def test_get_session_owned_for_update_locks_only_outer_chat_session_statement() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    repository = ChatRepository(cast(AsyncSession, session))

    await repository.get_session_owned_for_update(session_id=uuid4(), user_id=uuid4())

    statement = session.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert compiled.count("FOR UPDATE") == 1
    assert "FROM chat_session" in compiled
    assert "EXISTS (SELECT 1" in compiled
    assert "FROM prescription INNER JOIN medical_document" in compiled
    assert "FROM chat_session INNER JOIN" not in compiled


async def test_get_session_owned_for_update_returns_only_owners_session(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="chat-owner@example.com")
    intruder = await _create_user(db_session, email="chat-intruder@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    chat_session = ChatSession(prescription_id=prescription.id)
    db_session.add(chat_session)
    await db_session.flush()

    repository = ChatRepository(db_session)

    owned = await repository.get_session_owned_for_update(session_id=chat_session.id, user_id=owner.id)
    assert owned is not None
    assert owned.id == chat_session.id

    stolen = await repository.get_session_owned_for_update(session_id=chat_session.id, user_id=intruder.id)
    assert stolen is None


async def test_get_medications_orders_by_display_order_statement() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    repository = PrescriptionRepository(cast(AsyncSession, session))

    await repository.get_medications(prescription_id=uuid4())

    statement = session.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert "ORDER BY medication.display_order" in compiled


async def test_get_medications_returns_every_medication_in_display_order(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session, email="medication-owner@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    for display_order in (3, 1, 2):
        db_session.add(
            Medication(
                prescription_id=prescription.id,
                medication_name=f"합성 의약품 {display_order}",
                display_order=display_order,
            )
        )
    await db_session.flush()

    medications = await PrescriptionRepository(db_session).get_medications(prescription_id=prescription.id)

    assert [medication.display_order for medication in medications] == [1, 2, 3]
