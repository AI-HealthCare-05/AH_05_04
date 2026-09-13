from datetime import datetime, timedelta
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core import config
from app.core.utils.security import generate_email_verification_token, hash_email_verification_token
from app.dependencies.services import get_email_sender
from app.main import app, fastapi_app
from app.models.email_verification import EmailVerificationPurpose
from app.repositories.email_verification_repository import EmailVerificationRepository


class RecordingEmailSender:
    def __init__(self) -> None:
        self.email_verifications: list[tuple[str, str]] = []
        self.password_resets: list[tuple[str, str]] = []

    async def send_email_verification(self, *, email: str, token: str) -> None:
        self.email_verifications.append((email, token))

    async def send_password_reset(self, *, email: str, token: str) -> None:
        self.password_resets.append((email, token))


async def test_email_verification_request_issues_local_token_and_sends_email() -> None:
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    email = f"verify-request-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["detail"]
    assert body["verification_token"]
    assert sender.email_verifications == [(email, body["verification_token"])]


async def test_email_verification_request_does_not_create_duplicate_probe_for_existing_email() -> None:
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    email = f"verify-existing-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "인증중복테스터"},
            )
            response = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["detail"]
    assert body["verification_token"] is None
    assert sender.email_verifications == []


async def test_email_verification_request_within_cooldown_does_not_issue_new_token() -> None:
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    email = f"verify-cooldown-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
            second = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert first.status_code == status.HTTP_200_OK
    assert first.json()["verification_token"]
    assert second.status_code == status.HTTP_200_OK
    assert second.json()["verification_token"] is None
    assert len(sender.email_verifications) == 1


async def test_email_verification_confirm_accepts_valid_token_and_rejects_reuse() -> None:
    email = f"verify-confirm-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        request_response = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
        token = request_response.json()["verification_token"]

        confirm_response = await client.post(
            "/api/v1/auth/email-verification/confirm",
            json={"email": email, "token": token},
        )
        reuse_response = await client.post(
            "/api/v1/auth/email-verification/confirm",
            json={"email": email, "token": token},
        )

    assert confirm_response.status_code == status.HTTP_200_OK
    assert confirm_response.json() == {"detail": "이메일 인증이 완료되었습니다."}
    assert reuse_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert reuse_response.json()["details"] == [
        {"field": "token", "reason": "EMAIL_VERIFICATION_TOKEN_INVALID", "rejected_value": None}
    ]


async def test_email_verification_confirm_rejects_token_for_different_email() -> None:
    email = f"verify-owner-{uuid4().hex[:10]}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        request_response = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
        response = await client.post(
            "/api/v1/auth/email-verification/confirm",
            json={
                "email": f"other-{uuid4().hex[:10]}@example.com",
                "token": request_response.json()["verification_token"],
            },
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["details"] == [
        {"field": "token", "reason": "EMAIL_VERIFICATION_TOKEN_INVALID", "rejected_value": None}
    ]


async def test_email_verification_confirm_rejects_expired_token(db_session) -> None:
    email = f"verify-expired-{uuid4().hex[:10]}@example.com"
    raw_token = generate_email_verification_token()
    await EmailVerificationRepository(db_session).create_token(
        email=email,
        purpose=EmailVerificationPurpose.SIGNUP,
        token_hash=hash_email_verification_token(raw_token),
        expires_at=datetime.now(config.TIMEZONE) - timedelta(minutes=1),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/email-verification/confirm",
            json={"email": email, "token": raw_token},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["details"] == [
        {"field": "token", "reason": "EMAIL_VERIFICATION_TOKEN_INVALID", "rejected_value": None}
    ]


async def test_password_reset_request_uses_email_sender_for_existing_account() -> None:
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    email = f"reset-mail-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "재설정메일테스터"},
            )
            response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert response.status_code == status.HTTP_200_OK
    reset_token = response.json()["reset_token"]
    assert reset_token
    assert sender.password_resets == [(email, reset_token)]


async def test_password_reset_request_does_not_send_for_unknown_account() -> None:
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/password-reset/request",
                json={"email": f"unknown-mail-{uuid4().hex[:10]}@example.com"},
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["reset_token"] is None
    assert sender.password_resets == []
