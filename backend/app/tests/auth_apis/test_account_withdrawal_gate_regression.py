from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core import config
from app.main import app
from app.models.async_jobs import AiJob
from app.models.chat import ChatMessage
from app.models.guides import Guide
from app.models.users import User
from app.tests.helpers.auth import signup_verified_user

PASSWORD = "Password123!"


@pytest.fixture
def enable_account_withdrawal_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED", True)


async def _signup_login_and_request_withdrawal(client: AsyncClient) -> tuple[str, str]:
    email = f"withdrawal-gate-{uuid4().hex[:10]}@example.com"
    await signup_verified_user(client, {"email": email, "password": PASSWORD, "name": "탈퇴차단"})
    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login_response.status_code == status.HTTP_200_OK, login_response.text
    access_token = login_response.json()["access_token"]

    withdrawal_response = await client.post(
        "/api/v1/auth/account/withdrawal",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"password": PASSWORD, "confirmed": True},
    )
    assert withdrawal_response.status_code == status.HTTP_200_OK, withdrawal_response.text
    return access_token, email


async def _count_rows(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _assert_original_email_is_removed(session: AsyncSession, email: str) -> None:
    user = await session.scalar(select(User).where(User.email == email))
    assert user is None


def _assert_invalid_token(response) -> None:
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["code"] == "INVALID_TOKEN"


async def test_withdrawn_user_cannot_start_ocr_guide_or_chat(
    db_session: AsyncSession,
    enable_account_withdrawal_request: None,
) -> None:
    before_jobs = await _count_rows(db_session, AiJob)
    before_guides = await _count_rows(db_session, Guide)
    before_chat_messages = await _count_rows(db_session, ChatMessage)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, email = await _signup_login_and_request_withdrawal(client)
        await _assert_original_email_is_removed(db_session, email)
        headers = {"Authorization": f"Bearer {access_token}"}

        ocr_response = await client.post(
            f"/api/v1/documents/{uuid4()}/ocr-jobs",
            headers={**headers, "Idempotency-Key": "withdrawal-ocr-gate-0001"},
            json={"force_reprocess": False},
        )
        guide_response = await client.post(
            "/api/v1/guides",
            headers=headers,
            json={"prescription_id": str(uuid4())},
        )
        chat_response = await client.post(
            f"/api/v1/chat-sessions/{uuid4()}/messages",
            headers=headers,
            json={"content": "복약 안내를 알려주세요."},
        )

    _assert_invalid_token(ocr_response)
    _assert_invalid_token(guide_response)
    _assert_invalid_token(chat_response)
    assert await _count_rows(db_session, AiJob) == before_jobs
    assert await _count_rows(db_session, Guide) == before_guides
    assert await _count_rows(db_session, ChatMessage) == before_chat_messages
