import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core import config
from app.core.config import Env
from app.core.db.databases import get_db_session
from app.core.utils.security import generate_email_verification_token, hash_email_verification_token
from app.dependencies.services import get_email_sender
from app.main import app, fastapi_app
from app.models.email_verification import EmailVerificationPurpose
from app.repositories.email_verification_repository import EmailVerificationRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.user_repository import UserRepository
from app.services.email_delivery import EmailDeliveryError
from app.tests.conftest import test_engine


class FailingEmailSender:
    async def send_email_verification(self, *, email: str, token: str) -> None:
        raise EmailDeliveryError("Email delivery failed")

    async def send_password_reset(self, *, email: str, token: str) -> None:
        raise EmailDeliveryError("Email delivery failed")


class DelayedEmailSender:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.email_verifications: list[tuple[str, str]] = []
        self.password_resets: list[tuple[str, str]] = []

    async def send_email_verification(self, *, email: str, token: str) -> None:
        self.started.set()
        await self.release.wait()
        self.email_verifications.append((email, token))

    async def send_password_reset(self, *, email: str, token: str) -> None:
        self.started.set()
        await self.release.wait()
        self.password_resets.append((email, token))


async def _wait_until(assertion, *, timeout_seconds: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: AssertionError | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            assertion()
            return
        except AssertionError as exc:
            last_error = exc
            await asyncio.sleep(0.01)
    if last_error is not None:
        raise last_error
    assertion()


async def _wait_until_no_recent_email_verification_token(*, email: str) -> None:
    async def has_no_recent_token() -> bool:
        async with AsyncSession(bind=test_engine, expire_on_commit=False) as session:
            recent_token = await EmailVerificationRepository(session).find_recent_token(
                email=email,
                purpose=EmailVerificationPurpose.SIGNUP,
                since=datetime.now(config.TIMEZONE)
                - timedelta(seconds=config.EMAIL_VERIFICATION_REQUEST_COOLDOWN_SECONDS),
            )
            return recent_token is None

    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        if await has_no_recent_token():
            return
        await asyncio.sleep(0.01)
    assert await has_no_recent_token()


async def _wait_until_no_recent_password_reset_token(*, user_id: UUID) -> None:
    async def has_no_recent_token() -> bool:
        async with AsyncSession(bind=test_engine, expire_on_commit=False) as session:
            recent_token = await PasswordResetRepository(session).find_recent_token_for_user(
                user_id=user_id,
                since=datetime.now(config.TIMEZONE) - timedelta(seconds=config.PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS),
            )
            return recent_token is None

    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        if await has_no_recent_token():
            return
        await asyncio.sleep(0.01)
    assert await has_no_recent_token()


class RecordingEmailSender:
    def __init__(self) -> None:
        self.email_verifications: list[tuple[str, str]] = []
        self.password_resets: list[tuple[str, str]] = []

    async def send_email_verification(self, *, email: str, token: str) -> None:
        self.email_verifications.append((email, token))

    async def send_password_reset(self, *, email: str, token: str) -> None:
        self.password_resets.append((email, token))


async def _signup(client: AsyncClient, *, email: str, name: str = "인증테스터") -> None:
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Password123!", "name": name},
    )
    assert response.status_code == status.HTTP_201_CREATED


async def _independent_get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(bind=test_engine, expire_on_commit=False, autoflush=False) as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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

    def sent_once() -> None:
        assert sender.email_verifications == [(email, body["verification_token"])]

    await _wait_until(sent_once)


async def test_email_verification_request_does_not_create_duplicate_probe_for_existing_email() -> None:
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    email = f"verify-existing-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _signup(client, email=email, name="인증중복테스터")
            response = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["detail"]
    assert body["verification_token"] is None
    assert sender.email_verifications == []


async def test_email_verification_request_non_local_responses_do_not_reveal_account_or_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(config, "ENV", Env.PRODUCTION)
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    existing_email = f"evexist-{uuid4().hex[:10]}@example.com"
    unknown_email = f"evunknown-{uuid4().hex[:10]}@example.com"
    cooldown_email = f"evcool-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _signup(client, email=existing_email, name="인증응답테스터")
            existing_response = await client.post(
                "/api/v1/auth/email-verification/request", json={"email": existing_email}
            )
            unknown_response = await client.post(
                "/api/v1/auth/email-verification/request", json={"email": unknown_email}
            )
            first_cooldown_response = await client.post(
                "/api/v1/auth/email-verification/request", json={"email": cooldown_email}
            )
            cooldown_response = await client.post(
                "/api/v1/auth/email-verification/request", json={"email": cooldown_email}
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert existing_response.status_code == status.HTTP_200_OK
    assert unknown_response.status_code == status.HTTP_200_OK
    assert first_cooldown_response.status_code == status.HTTP_200_OK
    assert cooldown_response.status_code == status.HTTP_200_OK
    assert existing_response.json() == unknown_response.json() == cooldown_response.json()
    assert first_cooldown_response.json() == cooldown_response.json()
    assert existing_email.lower() not in existing_response.text.lower()
    assert unknown_email.lower() not in unknown_response.text.lower()
    assert cooldown_email.lower() not in cooldown_response.text.lower()

    def sent_for_non_existing_first_requests() -> None:
        assert [email for email, _ in sender.email_verifications] == [unknown_email, cooldown_email]

    await _wait_until(sent_for_non_existing_first_requests)


async def test_email_verification_request_concurrent_first_requests_issue_one_token() -> None:
    sender = RecordingEmailSender()

    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    fastapi_app.dependency_overrides[get_db_session] = _independent_get_db_session
    email = f"verify-concurrent-{uuid4().hex[:10]}@example.com"
    try:

        async def request_verification() -> Response:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await client.post("/api/v1/auth/email-verification/request", json={"email": email})

        responses = await asyncio.gather(request_verification(), request_verification())
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)
        fastapi_app.dependency_overrides.pop(get_db_session, None)

    assert [response.status_code for response in responses] == [status.HTTP_200_OK, status.HTTP_200_OK]
    tokens = [response.json()["verification_token"] for response in responses]
    assert sum(token is not None for token in tokens) == 1

    def sent_once() -> None:
        assert len(sender.email_verifications) == 1
        assert sender.email_verifications[0][0] == email

    await _wait_until(sent_once)


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

    def sent_once() -> None:
        assert len(sender.email_verifications) == 1

    await _wait_until(sent_once)


async def test_email_verification_request_delivery_failure_keeps_public_response(
    db_session,
) -> None:
    sender = FailingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    fastapi_app.dependency_overrides[get_db_session] = _independent_get_db_session
    email = f"vfail-{uuid4().hex[:10]}@example.com"
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)
        fastapi_app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["detail"]
    assert body["verification_token"]
    assert email.lower() not in response.text.lower()
    await _wait_until_no_recent_email_verification_token(email=email)

    retry_sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: retry_sender
    fastapi_app.dependency_overrides[get_db_session] = _independent_get_db_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            retry_response = await client.post("/api/v1/auth/email-verification/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)
        fastapi_app.dependency_overrides.pop(get_db_session, None)

    assert retry_response.status_code == status.HTTP_200_OK
    retry_token = retry_response.json()["verification_token"]
    assert retry_token

    def resent_after_cleanup() -> None:
        assert retry_sender.email_verifications == [(email, retry_token)]

    await _wait_until(resent_after_cleanup)


async def test_email_verification_request_returns_before_delayed_sender_finishes() -> None:
    sender = DelayedEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    email = f"evdelay-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(
                client.post("/api/v1/auth/email-verification/request", json={"email": email}),
                timeout=1.0,
            )

        assert response.status_code == status.HTTP_200_OK
        token = response.json()["verification_token"]
        assert token
        await asyncio.wait_for(sender.started.wait(), timeout=1.0)
        assert sender.email_verifications == []
        sender.release.set()

        def sent_after_release() -> None:
            assert sender.email_verifications == [(email, token)]

        await _wait_until(sent_after_release)
    finally:
        sender.release.set()
        fastapi_app.dependency_overrides.pop(get_email_sender, None)


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
            await _signup(client, email=email, name="재설정메일테스터")
            response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert response.status_code == status.HTTP_200_OK
    reset_token = response.json()["reset_token"]
    assert reset_token

    def sent_once() -> None:
        assert sender.password_resets == [(email, reset_token)]

    await _wait_until(sent_once)


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


async def test_password_reset_request_non_local_responses_do_not_reveal_account_or_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(config, "ENV", Env.PRODUCTION)
    sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    existing_email = f"prexist-{uuid4().hex[:10]}@example.com"
    unknown_email = f"prunknown-{uuid4().hex[:10]}@example.com"
    cooldown_email = f"prcool-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _signup(client, email=existing_email, name="재설정응답테스터")
            await _signup(client, email=cooldown_email, name="재설정쿨다운")
            existing_response = await client.post("/api/v1/auth/password-reset/request", json={"email": existing_email})
            unknown_response = await client.post("/api/v1/auth/password-reset/request", json={"email": unknown_email})
            first_cooldown_response = await client.post(
                "/api/v1/auth/password-reset/request", json={"email": cooldown_email}
            )
            cooldown_response = await client.post("/api/v1/auth/password-reset/request", json={"email": cooldown_email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)

    assert existing_response.status_code == status.HTTP_200_OK
    assert unknown_response.status_code == status.HTTP_200_OK
    assert first_cooldown_response.status_code == status.HTTP_200_OK
    assert cooldown_response.status_code == status.HTTP_200_OK
    assert existing_response.json() == unknown_response.json() == cooldown_response.json()
    assert first_cooldown_response.json() == cooldown_response.json()
    assert existing_email.lower() not in existing_response.text.lower()
    assert unknown_email.lower() not in unknown_response.text.lower()
    assert cooldown_email.lower() not in cooldown_response.text.lower()

    def sent_for_existing_first_requests() -> None:
        assert [email for email, _ in sender.password_resets] == [existing_email, cooldown_email]

    await _wait_until(sent_for_existing_first_requests)


async def test_password_reset_request_delivery_failure_matches_unknown_response_and_cleans_token(db_session) -> None:
    sender = FailingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    fastapi_app.dependency_overrides[get_db_session] = _independent_get_db_session
    email = f"rfail-{uuid4().hex[:10]}@example.com"
    unknown_email = f"unknown-rfail-{uuid4().hex[:10]}@example.com"
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _signup(client, email=email, name="발송실패테스터")
            existing_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
            unknown_response = await client.post("/api/v1/auth/password-reset/request", json={"email": unknown_email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)
        fastapi_app.dependency_overrides.pop(get_db_session, None)

    assert existing_response.status_code == status.HTTP_200_OK
    assert unknown_response.status_code == status.HTTP_200_OK
    assert existing_response.json()["detail"] == unknown_response.json()["detail"]
    assert existing_response.json()["reset_token"]
    assert unknown_response.json()["reset_token"] is None
    assert email.lower() not in existing_response.text.lower()
    user = await UserRepository(db_session).get_user_by_email(email)
    assert user is not None
    await _wait_until_no_recent_password_reset_token(user_id=user.id)

    retry_sender = RecordingEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: retry_sender
    fastapi_app.dependency_overrides[get_db_session] = _independent_get_db_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            retry_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
    finally:
        fastapi_app.dependency_overrides.pop(get_email_sender, None)
        fastapi_app.dependency_overrides.pop(get_db_session, None)

    assert retry_response.status_code == status.HTTP_200_OK
    retry_token = retry_response.json()["reset_token"]
    assert retry_token

    def resent_after_cleanup() -> None:
        assert retry_sender.password_resets == [(email, retry_token)]

    await _wait_until(resent_after_cleanup)


async def test_password_reset_request_returns_before_delayed_sender_finishes() -> None:
    sender = DelayedEmailSender()
    fastapi_app.dependency_overrides[get_email_sender] = lambda: sender
    email = f"prdelay-{uuid4().hex[:10]}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _signup(client, email=email, name="재설정지연")
            response = await asyncio.wait_for(
                client.post("/api/v1/auth/password-reset/request", json={"email": email}),
                timeout=1.0,
            )

        assert response.status_code == status.HTTP_200_OK
        reset_token = response.json()["reset_token"]
        assert reset_token
        await asyncio.wait_for(sender.started.wait(), timeout=1.0)
        assert sender.password_resets == []
        sender.release.set()

        def sent_after_release() -> None:
            assert sender.password_resets == [(email, reset_token)]

        await _wait_until(sent_after_release)
    finally:
        sender.release.set()
        fastapi_app.dependency_overrides.pop(get_email_sender, None)
