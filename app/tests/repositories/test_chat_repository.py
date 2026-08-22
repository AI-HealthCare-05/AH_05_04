from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ClauseElement

from app.models.chat import ChatGenerationStatus, ChatRole, ChatSession
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


async def _create_session(session: AsyncSession) -> tuple[User, Prescription, ChatSession]:
    token = uuid4().hex
    user = User(
        email=f"chat-{token[:12]}@example.com",
        hashed_password="hashed-password",
        name="합성 사용자",
        gender=Gender.MALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    session.add(user)
    await session.flush()
    document = MedicalDocument(
        user_id=user.id,
        original_file_name="synthetic.jpg",
        object_key=f"synthetic/{token}.jpg",
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
        confirmed_at=datetime.now(UTC),
    )
    session.add(prescription)
    await session.flush()
    chat_session = ChatSession(prescription_id=prescription.id)
    session.add(chat_session)
    await session.flush()
    return user, prescription, chat_session


class CapturingResult:
    def scalar_one_or_none(self) -> None:
        return None


class CapturingSession:
    def __init__(self) -> None:
        self.statement: object | None = None

    async def execute(self, statement: object) -> CapturingResult:
        self.statement = statement
        return CapturingResult()


async def test_get_session_owned_for_update_compiles_unique_outer_lock_with_correlated_ownership_exists() -> None:
    session = CapturingSession()
    repository = ChatRepository(session)  # type: ignore[arg-type]
    session_id = uuid4()
    user_id = uuid4()

    await repository.get_session_owned_for_update(session_id=session_id, user_id=user_id)

    assert session.statement is not None
    statement = cast(ClauseElement, session.statement)
    sql = " ".join(
        str(statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})).split()
    ).upper()
    assert sql.count("FOR UPDATE") == 1
    assert sql.endswith("FOR UPDATE")
    assert "FROM CHAT_SESSION" in sql
    assert "EXISTS (SELECT 1 FROM PRESCRIPTION INNER JOIN MEDICAL_DOCUMENT" in sql
    assert sql.count("CHAT_SESSION.ID =") == 1
    assert f"CHAT_SESSION.ID = '{session_id}'".upper() in sql
    assert "PRESCRIPTION.ID = CHAT_SESSION.PRESCRIPTION_ID" in sql
    assert f"MEDICAL_DOCUMENT.USER_ID = '{user_id}'".upper() in sql
    assert "CHAT_SESSION JOIN" not in sql


async def test_get_medications_orders_by_display_order_ascending_and_preserves_values(db_session: AsyncSession) -> None:
    _, prescription, _ = await _create_session(db_session)
    db_session.add_all(
        [
            Medication(
                prescription_id=prescription.id,
                medication_name="두 번째 합성약",
                dose_value=Decimal("2.500"),
                dose_unit="mg",
                frequency_per_day=1,
                timing_text="저녁",
                duration_days=5,
                display_order=2,
            ),
            Medication(
                prescription_id=prescription.id,
                medication_name="첫 번째 합성약",
                dose_value=Decimal("0.123"),
                dose_unit="mg",
                frequency_per_day=3,
                timing_text="식후",
                duration_days=9,
                display_order=1,
            ),
        ]
    )
    await db_session.flush()

    medications = await PrescriptionRepository(db_session).get_medications(prescription_id=prescription.id)

    assert [item.display_order for item in medications] == [1, 2]
    assert (
        medications[0].medication_name,
        medications[0].dose_value,
        medications[0].dose_unit,
        medications[0].frequency_per_day,
        medications[0].timing_text,
        medications[0].duration_days,
    ) == ("첫 번째 합성약", Decimal("0.123"), "mg", 3, "식후", 9)
    assert (
        medications[1].medication_name,
        medications[1].dose_value,
        medications[1].dose_unit,
        medications[1].frequency_per_day,
        medications[1].timing_text,
        medications[1].duration_days,
    ) == ("두 번째 합성약", Decimal("2.500"), "mg", 1, "저녁", 5)


async def test_commit_failed_message_pair_persists_exactly_one_user_failed_assistant_pair_after_rollback(
    db_session: AsyncSession,
) -> None:
    user, _, chat_session = await _create_session(db_session)
    repository = ChatRepository(db_session)
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
    assistant_message.content = "제거되어야 할 합성 답변"
    assistant_message.model_name = "stale-model"
    assistant_message.prompt_version = "stale-prompt"

    completed_at = datetime.now(UTC)
    await repository.commit_failed_message_pair(
        assistant_message,
        error_code="OPENAI_API_TIMEOUT",
        error_message="OpenAI 호출이 제한 시간 내에 완료되지 않았습니다.",
        completed_at=completed_at,
    )
    await db_session.rollback()

    owned = await repository.get_session_owned(session_id=chat_session.id, user_id=user.id)
    assert owned is not None
    messages = await repository.list_messages(session=owned)
    assert [message.id for message in messages] == [user_message.id, assistant_message.id]
    assert [message.message_seq for message in messages] == [1, 2]
    assert messages[1].generation_status == ChatGenerationStatus.FAILED
    assert messages[1].error_code == "OPENAI_API_TIMEOUT"
    assert messages[1].error_message == "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
    assert messages[1].completed_at is not None
    assert (messages[1].content, messages[1].model_name, messages[1].prompt_version) == (None, None, None)
