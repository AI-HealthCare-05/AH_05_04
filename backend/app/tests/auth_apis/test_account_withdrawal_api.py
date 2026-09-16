import re
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core import config
from app.main import app
from app.models.account_deletion_request import AccountDeletionRequest, AccountDeletionRequestStatus
from app.models.users import AccountStatus, User
from app.tests.helpers.auth import signup_verified_user

PASSWORD = "Password123!"


@pytest.fixture
def enable_account_withdrawal_request(monkeypatch):
    monkeypatch.setattr(config, "ACCOUNT_WITHDRAWAL_REQUEST_ENABLED", True)


def extract_refresh_token(response) -> str:
    set_cookie = response.headers.get("set-cookie", "")
    match = re.search(r"refresh_token=([^;]+)", set_cookie)
    assert match is not None
    return match.group(1)


async def signup_and_login(client: AsyncClient, *, email: str) -> tuple[str, str]:
    await signup_verified_user(client, {"email": email, "password": PASSWORD, "name": "탈퇴테스터"})
    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login_response.status_code == status.HTTP_200_OK, login_response.text
    return login_response.json()["access_token"], extract_refresh_token(login_response)


async def user_by_email(db_session: AsyncSession, email: str) -> User:
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def deletion_request_count(db_session: AsyncSession, user_id) -> int:
    return int(
        await db_session.scalar(
            select(func.count()).select_from(AccountDeletionRequest).where(AccountDeletionRequest.user_id == user_id)
        )
    )


async def test_account_withdrawal_is_closed_when_public_gate_disabled(db_session: AsyncSession):
    email = f"withdrawal-gate-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": True},
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["details"] == [
        {
            "field": "account_withdrawal",
            "reason": "ACCOUNT_WITHDRAWAL_REQUEST_DISABLED",
            "rejected_value": None,
        }
    ]
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert user.token_version == 0
    assert await deletion_request_count(db_session, user.id) == 0


async def test_account_withdrawal_success_marks_user_and_creates_pending_request(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"withdrawal-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, refresh_token = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": True},
        )
        user_me_response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"})
        client.cookies["refresh_token"] = refresh_token
        refresh_response = await client.get("/api/v1/auth/token/refresh")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"detail": "계정 이용 종료와 탈퇴 요청 접수가 완료되었습니다."}
    assert "refresh_token=" in response.headers.get("set-cookie", "")

    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.WITHDRAWAL_REQUESTED
    assert user.is_active is False
    assert user.withdrawal_requested_at is not None
    assert user.withdrawn_at is None
    assert user.token_version == 1

    request = await db_session.scalar(select(AccountDeletionRequest).where(AccountDeletionRequest.user_id == user.id))
    assert request is not None
    assert request.status == AccountDeletionRequestStatus.PENDING
    assert request.requested_at is not None

    assert user_me_response.status_code == status.HTTP_401_UNAUTHORIZED
    assert user_me_response.json()["code"] == "INVALID_TOKEN"
    assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED
    assert refresh_response.json()["code"] == "INVALID_TOKEN"


async def test_account_withdrawal_rejects_wrong_password_without_side_effect(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"withdrawal-wrong-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": "WrongPassword123!", "confirmed": True},
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["code"] == "UNAUTHORIZED"
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert user.withdrawal_requested_at is None
    assert user.token_version == 0
    assert await deletion_request_count(db_session, user.id) == 0


async def test_account_withdrawal_requires_confirmation_without_side_effect(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"wd-confirm-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": False},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["details"] == [{"field": "confirmed", "reason": "CONFIRMATION_REQUIRED", "rejected_value": None}]
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert user.withdrawal_requested_at is None
    assert user.token_version == 0
    assert await deletion_request_count(db_session, user.id) == 0


async def test_account_withdrawal_rejects_user_id_in_body_without_side_effect(
    db_session: AsyncSession, enable_account_withdrawal_request
):
    email = f"withdrawal-extra-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        access_token, _ = await signup_and_login(client, email=email)
        response = await client.post(
            "/api/v1/auth/account/withdrawal",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"password": PASSWORD, "confirmed": True, "user_id": str(uuid4())},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    user = await user_by_email(db_session, email)
    assert user.account_status == AccountStatus.ACTIVE
    assert user.is_active is True
    assert await deletion_request_count(db_session, user.id) == 0
