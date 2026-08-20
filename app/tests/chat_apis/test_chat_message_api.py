from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response

from app.core.errors import ApiError, ErrorDetail
from app.dependencies.security import get_request_user
from app.dependencies.services import get_chat_service
from app.dtos.chat import ChatMessageData, ChatRole, ChatSessionData, SendChatMessageData
from app.main import app, fastapi_app

TEST_ORIGIN = "http://localhost:5173"
SESSION_ID = UUID("00000000-0000-0000-0000-000000000101")


@pytest.fixture(scope="session", autouse=True)
def initialize_database() -> None:
    """Keep these ASGI contract tests independent from the local MySQL fixture."""


@pytest.fixture(autouse=True)
def isolate_database() -> None:
    """The endpoint dependencies below replace all database-backed services."""


class StubChatService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def send_message(self, *, user: object, session_id: UUID, request: object) -> SendChatMessageData:
        if self.error is not None:
            raise self.error
        now = datetime.now(UTC)
        return SendChatMessageData(
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
            session_id=session_id,
            generation_status="COMPLETED",
            content="합성 답변",
            model_name="gpt-4o-mini-2024-07-18",
            prompt_version="chat-prompt-v1",
            created_at=now,
            completed_at=now,
        )

    async def create_session(self, *, user: object, prescription_id: UUID) -> ChatSessionData:
        if self.error is not None:
            raise self.error
        return ChatSessionData(
            session_id=SESSION_ID,
            prescription_id=prescription_id,
            session_status="ACTIVE",
            created_at=datetime.now(UTC),
        )

    async def list_messages(self, *, user: object, session_id: UUID) -> list[ChatMessageData]:
        if self.error is not None:
            raise self.error
        return [
            ChatMessageData(
                message_id=uuid4(),
                role=ChatRole.ASSISTANT,
                content="합성 답변",
                generation_status="COMPLETED",
                created_at=datetime.now(UTC),
            )
        ]


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as test_client:
            yield test_client
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)


async def test_send_message_returns_stored_ai_result_with_no_store(client: AsyncClient) -> None:
    response = await client.post(
        f"/api/v1/chat-sessions/{SESSION_ID}/messages",
        json={"content": "현재 질문"},
        headers={"Origin": TEST_ORIGIN},
    )

    assert response.status_code == 201
    assert response.json()["data"]["session_id"] == str(SESSION_ID)
    assert response.json()["data"]["content"] == "합성 답변"
    assert response.json()["data"]["model_name"] == "gpt-4o-mini-2024-07-18"
    assert response.json()["data"]["prompt_version"] == "chat-prompt-v1"
    assert "trace_id" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_create_session_success_keeps_body_and_no_store(client: AsyncClient) -> None:
    prescription_id = uuid4()
    response = await client.post(
        f"/api/v1/prescriptions/{prescription_id}/chat-sessions",
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 201
    assert response.json()["data"]["session_id"] == str(SESSION_ID)
    assert response.json()["data"]["prescription_id"] == str(prescription_id)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_list_messages_success_keeps_body_and_no_store(client: AsyncClient) -> None:
    response = await client.get(
        f"/api/v1/chat-sessions/{SESSION_ID}/messages",
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 200
    assert response.json()["data"]["session_id"] == str(SESSION_ID)
    assert response.json()["data"]["messages"][0]["content"] == "합성 답변"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


@pytest.mark.parametrize(
    "error",
    [
        ApiError(
            status_code=500,
            code="AI_RESPONSE_FAILED",
            message="AI 답변 생성에 실패했습니다.",
            details=[ErrorDetail(field="assistant_message", reason="OPENAI_RESPONSE_PROCESSING_FAILED")],
        ),
        ApiError(
            status_code=503,
            code="SERVICE_UNAVAILABLE",
            message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            details=[ErrorDetail(field="openai_api", reason="OPENAI_API_ERROR")],
        ),
        ApiError(
            status_code=504,
            code="GATEWAY_TIMEOUT",
            message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
            details=[ErrorDetail(field="openai_api", reason="OPENAI_API_TIMEOUT")],
        ),
    ],
)
async def test_send_message_errors_keep_common_body_cors_and_no_store(error: ApiError) -> None:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService(error=error)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/chat-sessions/{SESSION_ID}/messages",
                json={"content": "현재 질문"},
                headers={"Origin": TEST_ORIGIN},
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)

    body = response.json()
    assert response.status_code == error.status_code
    assert body["code"] == error.code
    assert body["message"] == error.message
    assert body["details"] == [detail.model_dump(mode="json") for detail in error.details]
    assert body["trace_id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


def assert_private_chat_error(response: Response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.json()["trace_id"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/chat-sessions/not-a-uuid/messages",
        "/api/v1/prescriptions/not-a-uuid/chat-sessions",
    ],
)
async def test_invalid_chat_identifiers_keep_no_store(path: str) -> None:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(path, json={"content": "현재 질문"})
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)
    assert_private_chat_error(response, status_code=422, code="VALIDATION_FAILED")


async def reject_user() -> None:
    raise HTTPException(
        status_code=401,
        detail="인증이 필요합니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def test_authentication_error_preserves_www_authenticate_cors_and_no_store() -> None:
    fastapi_app.dependency_overrides[get_request_user] = reject_user
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/chat-sessions/{SESSION_ID}/messages",
                json={"content": "현재 질문"},
                headers={"Origin": TEST_ORIGIN},
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)

    assert_private_chat_error(response, status_code=401, code="HTTP_ERROR")
    assert response.json()["message"] == "인증이 필요합니다."
    assert response.json()["details"] == []
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_unexpected_error_keeps_common_error_cors_and_no_store() -> None:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    fastapi_app.dependency_overrides[get_chat_service] = lambda: StubChatService(
        error=RuntimeError("synthetic internal failure")
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/chat-sessions/{SESSION_ID}/messages",
                json={"content": "현재 질문"},
                headers={"Origin": TEST_ORIGIN},
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_chat_service, None)

    assert_private_chat_error(response, status_code=500, code="INTERNAL_SERVER_ERROR")
    assert response.json()["message"] == "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
    assert response.json()["details"] == []
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


async def test_cors_preflight_is_not_marked_no_store() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.options(
            f"/api/v1/chat-sessions/{SESSION_ID}/messages",
            headers={
                "Origin": TEST_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
    assert "cache-control" not in response.headers
