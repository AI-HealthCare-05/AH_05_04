from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatGenerationStatus, ChatMessage, ChatRole, ChatSession
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


async def test_mark_failed_assigns_safe_metadata_and_commits() -> None:
    session = AsyncMock()
    repository = ChatRepository(cast(AsyncSession, session))
    message = SimpleNamespace(
        generation_status=ChatGenerationStatus.GENERATING,
        error_code=None,
        error_message=None,
        completed_at=None,
    )
    completed_at = datetime(2026, 8, 20, 9, 1, tzinfo=UTC)

    result = await repository.mark_failed(
        cast(ChatMessage, message),
        error_code="OPENAI_API_TIMEOUT",
        error_message="OpenAI 호출이 제한 시간 내에 완료되지 않았습니다.",
        completed_at=completed_at,
    )

    assert result is message
    assert message.generation_status == ChatGenerationStatus.FAILED
    assert message.error_code == "OPENAI_API_TIMEOUT"
    assert message.error_message == "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
    assert message.completed_at == completed_at
    session.commit.assert_awaited_once_with()


async def test_mark_failed_persists_safe_metadata_and_user_row_after_subsequent_rollback(
    db_session: AsyncSession,
) -> None:
    owner = await _create_user(db_session, email="failed-chat-owner@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=owner)
    repository = ChatRepository(db_session)
    chat_session = await repository.create_session(prescription=prescription)
    user_message = await repository.create_message(
        session=chat_session,
        message_seq=1,
        role=ChatRole.USER,
        content="합성 질문",
        generation_status=ChatGenerationStatus.NOT_APPLICABLE,
    )
    assistant_message = await repository.create_message(
        session=chat_session,
        message_seq=2,
        role=ChatRole.ASSISTANT,
        content=None,
        generation_status=ChatGenerationStatus.PENDING,
    )
    await repository.mark_generating(assistant_message)
    chat_session_id = chat_session.id
    user_message_id = user_message.id

    await repository.mark_failed(
        assistant_message,
        error_code="OPENAI_API_TIMEOUT",
        error_message="OpenAI 호출이 제한 시간 내에 완료되지 않았습니다.",
        completed_at=datetime(2026, 8, 20, 9, 1, tzinfo=UTC),
    )
    await db_session.rollback()
    db_session.expunge_all()

    result = await db_session.execute(
        select(ChatMessage).where(ChatMessage.session_id == chat_session_id).order_by(ChatMessage.message_seq)
    )
    reloaded_user, reloaded_assistant = result.scalars().all()

    assert reloaded_user.id == user_message_id
    assert reloaded_user.role == ChatRole.USER
    assert reloaded_user.content == "합성 질문"
    assert reloaded_assistant.generation_status == ChatGenerationStatus.FAILED
    assert reloaded_assistant.error_code == "OPENAI_API_TIMEOUT"
    assert reloaded_assistant.error_message == "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
    assert reloaded_assistant.completed_at is not None
    assert reloaded_assistant.content is None
    assert reloaded_assistant.model_name is None
    assert reloaded_assistant.prompt_version is None
