from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.databases import get_db_session
from app.core.errors import ApiError
from app.dependencies.security import get_request_user
from app.dependencies.services import get_chat_engine, get_chat_service
from app.main import app, fastapi_app
from app.models.chat import ChatGenerationStatus, ChatMessage, ChatRole, ChatSession, ChatSessionStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Medication, Prescription
from app.models.users import Gender, User
from app.repositories.chat_repository import ChatRepository
from app.services.chat_ai import (
    ChatGenerationFailedError,
    ChatReplyInput,
    ChatReplyOutput,
    ChatServiceUnavailableError,
    ChatTimeoutError,
)
from app.tests.conftest import test_engine

TEST_ORIGIN = "http://localhost:5173"
SAFE_TIMEOUT_MESSAGE = "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."


@dataclass(frozen=True)
class ApiChatFixture:
    owner_id: UUID
    active_session_id: UUID
    closed_session_id: UUID
    foreign_session_id: UUID
    owner_prescription_id: UUID
    foreign_prescription_id: UUID


class FakeChatEngine:
    def __init__(
        self,
        *,
        result: ChatReplyOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = (
            result
            if result is not None
            else ChatReplyOutput(
                content="저장된 합성 답변",
                model_name="synthetic-model-2026-08",
                prompt_version="chat-prompt-test-v1",
            )
        )
        self.error = error
        self.inputs: list[ChatReplyInput] = []

    async def reply(self, chat_input: ChatReplyInput) -> ChatReplyOutput:
        self.inputs.append(chat_input)
        if self.error is not None:
            raise self.error
        return self.result


@pytest_asyncio.fixture
async def api_db_session() -> AsyncIterator[AsyncSession]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        previous_override = fastapi_app.dependency_overrides.get(get_db_session)

        async def override_get_db_session() -> AsyncIterator[AsyncSession]:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        fastapi_app.dependency_overrides[get_db_session] = override_get_db_session
        try:
            yield session
        finally:
            if previous_override is None:
                fastapi_app.dependency_overrides.pop(get_db_session, None)
            else:
                fastapi_app.dependency_overrides[get_db_session] = previous_override
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


async def _add_prescription_graph(session: AsyncSession, *, user: User, token: str) -> Prescription:
    document = MedicalDocument(
        user_id=user.id,
        original_file_name=f"synthetic-{token}.jpg",
        object_key=f"synthetic/chat-api/{token}.jpg",
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
    session.add(
        Medication(
            prescription_id=prescription.id,
            medication_name=f"합성약-{token}",
            dose_value=Decimal("1.250"),
            dose_unit="mg",
            frequency_per_day=2,
            timing_text="식후",
            duration_days=7,
            display_order=1,
        )
    )
    await session.flush()
    return prescription


@pytest_asyncio.fixture
async def api_chat_fixture(api_db_session: AsyncSession) -> ApiChatFixture:
    owner = User(
        email=f"cao-{uuid4().hex[:12]}@example.com",
        hashed_password="synthetic-hash",
        name="합성 소유자",
        gender=Gender.FEMALE,
        birthday=date(1990, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    outsider = User(
        email=f"cax-{uuid4().hex[:12]}@example.com",
        hashed_password="synthetic-hash",
        name="합성 타인",
        gender=Gender.MALE,
        birthday=date(1991, 1, 1),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    api_db_session.add_all([owner, outsider])
    await api_db_session.flush()
    owner_prescription = await _add_prescription_graph(api_db_session, user=owner, token=uuid4().hex)
    foreign_prescription = await _add_prescription_graph(api_db_session, user=outsider, token=uuid4().hex)
    active = ChatSession(prescription_id=owner_prescription.id)
    closed = ChatSession(
        prescription_id=owner_prescription.id,
        session_status=ChatSessionStatus.CLOSED,
    )
    foreign = ChatSession(prescription_id=foreign_prescription.id)
    api_db_session.add_all([active, closed, foreign])
    await api_db_session.flush()
    await api_db_session.commit()
    return ApiChatFixture(
        owner_id=owner.id,
        active_session_id=active.id,
        closed_session_id=closed.id,
        foreign_session_id=foreign.id,
        owner_prescription_id=owner_prescription.id,
        foreign_prescription_id=foreign_prescription.id,
    )


@pytest_asyncio.fixture
async def client(api_db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    del api_db_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_chat_dependency_overrides() -> Iterator[None]:
    yield
    fastapi_app.dependency_overrides.pop(get_request_user, None)
    fastapi_app.dependency_overrides.pop(get_chat_engine, None)
    fastapi_app.dependency_overrides.pop(get_chat_service, None)


def _use_owner_and_engine(fixture: ApiChatFixture, engine: FakeChatEngine) -> None:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=fixture.owner_id)
    fastapi_app.dependency_overrides[get_chat_engine] = lambda: engine


def _assert_private_error(
    response: Response,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]],
) -> None:
    body = response.json()
    assert response.status_code == status_code
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["trace_id"]
    assert body == {
        "code": code,
        "message": message,
        "details": details,
        "trace_id": body["trace_id"],
    }
    assert response.headers.get_list("cache-control") == ["no-store"]


async def test_send_message_201_matches_completed_assistant_persisted_content_and_metadata(
    client: AsyncClient,
    api_db_session: AsyncSession,
    api_chat_fixture: ApiChatFixture,
) -> None:
    engine = FakeChatEngine()
    _use_owner_and_engine(api_chat_fixture, engine)

    response = await client.post(
        f"/api/v1/chat-sessions/{api_chat_fixture.active_session_id}/messages",
        json={"content": "현재 합성 질문"},
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assistant = await api_db_session.get(ChatMessage, UUID(body["assistant_message_id"]))
    user_message = await api_db_session.get(ChatMessage, UUID(body["user_message_id"]))
    assert assistant is not None
    assert user_message is not None
    assert (assistant.role, assistant.generation_status) == (ChatRole.ASSISTANT, ChatGenerationStatus.COMPLETED)
    assert (
        (body["content"], body["model_name"], body["prompt_version"])
        == (
            assistant.content,
            assistant.model_name,
            assistant.prompt_version,
        )
        == ("\uc800\uc7a5\ub41c \ud569\uc131 \ub2f5\ubcc0", "synthetic-model-2026-08", "chat-prompt-test-v1")
    )
    assert user_message.content == "현재 합성 질문"
    assert engine.inputs[0].content == "현재 합성 질문"
    assert response.headers.get_list("cache-control") == ["no-store"]
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_create_and_list_route_successes_have_exact_no_store(
    client: AsyncClient,
    api_chat_fixture: ApiChatFixture,
) -> None:
    _use_owner_and_engine(api_chat_fixture, FakeChatEngine())

    create_response = await client.post(f"/api/v1/prescriptions/{api_chat_fixture.owner_prescription_id}/chat-sessions")
    list_response = await client.get(f"/api/v1/chat-sessions/{api_chat_fixture.active_session_id}/messages")

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert create_response.headers.get_list("cache-control") == ["no-store"]
    assert list_response.headers.get_list("cache-control") == ["no-store"]


@pytest.mark.parametrize(
    ("engine_error", "status_code", "code", "message", "field", "reason"),
    [
        (
            ChatGenerationFailedError("raw synthetic provider response"),
            500,
            "AI_RESPONSE_FAILED",
            "AI 답변 생성에 실패했습니다.",
            "assistant_message",
            "OPENAI_RESPONSE_PROCESSING_FAILED",
        ),
        (
            ChatServiceUnavailableError("raw synthetic provider body"),
            503,
            "SERVICE_UNAVAILABLE",
            "현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            "openai_api",
            "OPENAI_API_ERROR",
        ),
        (
            ChatTimeoutError("raw synthetic timeout"),
            504,
            "GATEWAY_TIMEOUT",
            "외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
            "openai_api",
            "OPENAI_API_TIMEOUT",
        ),
    ],
)
async def test_mapped_generation_errors_keep_exact_common_body_and_no_store(
    client: AsyncClient,
    api_chat_fixture: ApiChatFixture,
    engine_error: Exception,
    status_code: int,
    code: str,
    message: str,
    field: str,
    reason: str,
) -> None:
    _use_owner_and_engine(api_chat_fixture, FakeChatEngine(error=engine_error))

    response = await client.post(
        f"/api/v1/chat-sessions/{api_chat_fixture.active_session_id}/messages",
        json={"content": "오류 매핑용 합성 질문"},
    )

    _assert_private_error(
        response,
        status_code=status_code,
        code=code,
        message=message,
        details=[{"field": field, "reason": reason, "rejected_value": None}],
    )
    assert "raw synthetic" not in response.text


@pytest.mark.parametrize(
    ("method", "path", "json_body", "field"),
    [
        ("POST", "/api/v1/chat-sessions/not-a-uuid/messages", {"content": "합성 질문"}, "path.session_id"),
        ("GET", "/api/v1/chat-sessions/not-a-uuid/messages", None, "path.session_id"),
        ("POST", "/api/v1/prescriptions/not-a-uuid/chat-sessions", None, "path.prescription_id"),
    ],
)
async def test_malformed_uuid_422_has_exact_no_store_on_both_route_shapes(
    client: AsyncClient,
    api_chat_fixture: ApiChatFixture,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
    field: str,
) -> None:
    _use_owner_and_engine(api_chat_fixture, FakeChatEngine())
    response = await client.request(method, path, json=json_body)
    _assert_private_error(
        response,
        status_code=422,
        code="VALIDATION_FAILED",
        message="입력값을 확인해 주세요.",
        details=[{"field": field, "reason": "INVALID_FORMAT", "rejected_value": None}],
    )


async def test_empty_message_validation_has_exact_no_store(
    client: AsyncClient,
    api_chat_fixture: ApiChatFixture,
) -> None:
    _use_owner_and_engine(api_chat_fixture, FakeChatEngine())
    response = await client.post(
        f"/api/v1/chat-sessions/{api_chat_fixture.active_session_id}/messages",
        json={"content": ""},
    )
    _assert_private_error(
        response,
        status_code=422,
        code="VALIDATION_FAILED",
        message="입력값을 확인해 주세요.",
        details=[{"field": "content", "reason": "INVALID_FORMAT", "rejected_value": None}],
    )


async def _reject_user() -> None:
    raise ApiError(
        status_code=401,
        code="UNAUTHORIZED",
        message="로그인이 필요합니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("POST", f"/api/v1/chat-sessions/{uuid4()}/messages", {"content": "합성 질문"}),
        ("GET", f"/api/v1/chat-sessions/{uuid4()}/messages", None),
        ("POST", f"/api/v1/prescriptions/{uuid4()}/chat-sessions", None),
    ],
)
async def test_auth_error_preserves_www_authenticate_and_exact_no_store_on_both_route_shapes(
    client: AsyncClient,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
) -> None:
    fastapi_app.dependency_overrides[get_request_user] = _reject_user
    fastapi_app.dependency_overrides[get_chat_engine] = lambda: FakeChatEngine()
    response = await client.request(method, path, json=json_body, headers={"Origin": TEST_ORIGIN})
    _assert_private_error(
        response,
        status_code=401,
        code="UNAUTHORIZED",
        message="로그인이 필요합니다.",
        details=[],
    )
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_foreign_ownership_and_closed_session_are_rejected_before_engine(
    client: AsyncClient,
    api_chat_fixture: ApiChatFixture,
) -> None:
    engine = FakeChatEngine()
    _use_owner_and_engine(api_chat_fixture, engine)

    foreign_send = await client.post(
        f"/api/v1/chat-sessions/{api_chat_fixture.foreign_session_id}/messages",
        json={"content": "타인 세션 질문"},
    )
    foreign_create = await client.post(
        f"/api/v1/prescriptions/{api_chat_fixture.foreign_prescription_id}/chat-sessions"
    )
    closed_send = await client.post(
        f"/api/v1/chat-sessions/{api_chat_fixture.closed_session_id}/messages",
        json={"content": "종료 세션 질문"},
    )

    _assert_private_error(
        foreign_send,
        status_code=404,
        code="CHAT_SESSION_NOT_FOUND",
        message="대화 세션을 찾을 수 없습니다.",
        details=[
            {
                "field": "session_id",
                "reason": "NOT_FOUND",
                "rejected_value": str(api_chat_fixture.foreign_session_id),
            }
        ],
    )
    _assert_private_error(
        foreign_create,
        status_code=404,
        code="PRESCRIPTION_NOT_FOUND",
        message="처방 정보를 찾을 수 없습니다.",
        details=[
            {
                "field": "prescription_id",
                "reason": "NOT_FOUND",
                "rejected_value": str(api_chat_fixture.foreign_prescription_id),
            }
        ],
    )
    _assert_private_error(
        closed_send,
        status_code=409,
        code="CONFLICT",
        message="종료된 대화 세션에는 메시지를 추가할 수 없습니다.",
        details=[
            {
                "field": "session_id",
                "reason": "CHAT_SESSION_CLOSED",
                "rejected_value": str(api_chat_fixture.closed_session_id),
            }
        ],
    )
    assert engine.inputs == []


class UnexpectedChatService:
    async def send_message(self, **kwargs: object) -> None:
        del kwargs
        raise RuntimeError("synthetic unexpected chat failure")

    async def create_session(self, **kwargs: object) -> None:
        del kwargs
        raise RuntimeError("synthetic unexpected chat failure")

    async def list_messages(self, **kwargs: object) -> None:
        del kwargs
        raise RuntimeError("synthetic unexpected chat failure")


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("POST", f"/api/v1/chat-sessions/{uuid4()}/messages", {"content": "합성 질문"}),
        ("GET", f"/api/v1/chat-sessions/{uuid4()}/messages", None),
        ("POST", f"/api/v1/prescriptions/{uuid4()}/chat-sessions", None),
    ],
)
async def test_unexpected_500_keeps_cors_and_exact_no_store_on_both_route_shapes(
    client: AsyncClient,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
) -> None:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: UnexpectedChatService()
    response = await client.request(method, path, json=json_body, headers={"Origin": TEST_ORIGIN})
    _assert_private_error(
        response,
        status_code=500,
        code="INTERNAL_SERVER_ERROR",
        message="서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        details=[],
    )
    assert "synthetic unexpected" not in response.text
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_failed_send_is_requeried_as_exact_user_and_failed_assistant_pair(
    client: AsyncClient,
    api_db_session: AsyncSession,
    api_chat_fixture: ApiChatFixture,
) -> None:
    _use_owner_and_engine(api_chat_fixture, FakeChatEngine(error=ChatTimeoutError("raw timeout detail")))
    question = "재조회 확인용 합성 질문"

    failed_response = await client.post(
        f"/api/v1/chat-sessions/{api_chat_fixture.active_session_id}/messages",
        json={"content": question},
    )
    list_response = await client.get(f"/api/v1/chat-sessions/{api_chat_fixture.active_session_id}/messages")

    _assert_private_error(
        failed_response,
        status_code=504,
        code="GATEWAY_TIMEOUT",
        message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
        details=[{"field": "openai_api", "reason": "OPENAI_API_TIMEOUT", "rejected_value": None}],
    )
    assert list_response.status_code == 200
    listed = list_response.json()["data"]["messages"]
    assert [(item["role"], item["generation_status"], item["content"]) for item in listed] == [
        ("USER", "NOT_APPLICABLE", question),
        ("ASSISTANT", "FAILED", None),
    ]
    stored = list(
        (
            await api_db_session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == api_chat_fixture.active_session_id)
                .order_by(ChatMessage.message_seq)
            )
        )
        .scalars()
        .all()
    )
    assert len(stored) == 2
    assert (stored[1].error_code, stored[1].error_message) == ("OPENAI_API_TIMEOUT", SAFE_TIMEOUT_MESSAGE)
    assert stored[1].completed_at is not None
    assert (stored[1].content, stored[1].model_name, stored[1].prompt_version) == (None, None, None)


async def test_lock_wait_timeout_returns_common_500_and_creates_no_message(
    client: AsyncClient,
    api_db_session: AsyncSession,
    api_chat_fixture: ApiChatFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeChatEngine()
    _use_owner_and_engine(api_chat_fixture, engine)
    before = await api_db_session.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.session_id == api_chat_fixture.active_session_id)
    )

    async def raise_lock_wait_timeout(self: ChatRepository, *, session_id: UUID, user_id: UUID) -> None:
        del self, session_id, user_id
        raise OperationalError(
            "SELECT chat_session ... FOR UPDATE",
            {},
            RuntimeError("(1205, 'Lock wait timeout exceeded')"),
        )

    monkeypatch.setattr(ChatRepository, "get_session_owned_for_update", raise_lock_wait_timeout)
    response = await client.post(
        f"/api/v1/chat-sessions/{api_chat_fixture.active_session_id}/messages",
        json={"content": "잠금 대기 합성 질문"},
    )
    after = await api_db_session.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.session_id == api_chat_fixture.active_session_id)
    )

    _assert_private_error(
        response,
        status_code=500,
        code="INTERNAL_SERVER_ERROR",
        message="서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        details=[],
    )
    assert before == after == 0
    assert engine.inputs == []
