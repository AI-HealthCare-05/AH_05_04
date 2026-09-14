from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from app.core import config
from app.dependencies.services import get_user_consent_repository, get_user_repository
from app.main import app, fastapi_app
from app.models.user_consents import ConsentPurpose, ConsentStatus, UserConsent
from app.models.users import User
from app.repositories.user_repository import DuplicateUserFieldError, UserRepository

OCR_CONSENT = {"purpose": "OCR"}
GUIDE_CONSENT = {"purpose": "GUIDE"}
CHAT_CONSENT = {"purpose": "CHAT"}
NOTIFICATION_CONSENT = {"purpose": "NOTIFICATION"}


def _email() -> str:
    return f"u{uuid4().hex[:8]}@e.co"


async def _signup(
    client: AsyncClient,
    *,
    email: str,
    consents: list[dict[str, str]] | None = None,
    mark_signup_email_verified=None,
) -> Response:
    if mark_signup_email_verified is not None:
        await mark_signup_email_verified(email)
    payload: dict[str, Any] = {
        "email": email,
        "password": "Password123!",
        "name": "동의가입테스터",
    }
    if consents is not None:
        payload["consents"] = consents
    return await client.post("/api/v1/auth/signup", json=payload)


async def _consents_for_email(db_session: AsyncSession, *, email: str) -> list[UserConsent]:
    user_id = await db_session.scalar(select(User.id).where(User.email == email.lower()))
    if user_id is None:
        return []
    rows = await db_session.scalars(
        select(UserConsent).where(UserConsent.user_id == user_id).order_by(UserConsent.purpose)
    )
    return list(rows.all())


class TestSignupAPI:
    async def test_signup_success(self, mark_signup_email_verified):
        signup_data = {
            "email": "test@example.com",
            "password": "Password123!",
            "name": "테스터",
        }

        await mark_signup_email_verified(signup_data["email"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"detail": "회원가입이 성공적으로 완료되었습니다."}
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_signup_allows_unverified_email_when_verification_gate_is_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(config, "SIGNUP_EMAIL_VERIFICATION_REQUIRED", False)
        signup_data = {
            "email": "gate-disabled@example.com",
            "password": "Password123!",
            "name": "인증비활성테스터",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"detail": "회원가입이 성공적으로 완료되었습니다."}

    async def test_signup_requires_verified_email_when_verification_gate_is_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(config, "SIGNUP_EMAIL_VERIFICATION_REQUIRED", True)
        signup_data = {
            "email": "unverified@example.com",
            "password": "Password123!",
            "name": "미인증테스터",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)

        assert response.status_code == status.HTTP_409_CONFLICT
        body = response.json()
        assert body["code"] == "EMAIL_VERIFICATION_REQUIRED"
        assert body["details"] == [
            {
                "field": "email",
                "reason": "EMAIL_VERIFICATION_REQUIRED",
                "rejected_value": None,
            }
        ]
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_signup_rejects_expired_verified_email(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        mark_expired_signup_email_verified,
    ):
        monkeypatch.setattr(config, "SIGNUP_EMAIL_VERIFICATION_REQUIRED", True)
        email = "expired-verified@example.com"
        await mark_expired_signup_email_verified(email)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(client, email=email)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == "EMAIL_VERIFICATION_REQUIRED"
        assert await db_session.scalar(select(User.id).where(User.email == email)) is None

    async def test_signup_invalid_email(self):
        signup_data = {
            "email": "invalid-email",
            "password": "password123!",
            "name": "테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_signup_returns_conflict_for_concurrent_duplicate_email(
        self,
        mark_signup_email_verified,
    ):
        repository = AsyncMock(spec=UserRepository)
        repository.exists_by_email.return_value = False
        repository.create_user.side_effect = DuplicateUserFieldError("email")

        def override_get_user_repository():
            return repository

        fastapi_app.dependency_overrides[get_user_repository] = override_get_user_repository

        signup_data = {
            "email": "race@example.com",
            "password": "Password123!",
            "name": "동시가입테스트",
        }

        await mark_signup_email_verified(signup_data["email"])
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/v1/auth/signup",
                    json=signup_data,
                )
        finally:
            fastapi_app.dependency_overrides.pop(
                get_user_repository,
                None,
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        body = response.json()
        assert body["code"] == "CONFLICT"
        assert body["message"] == "이미 사용중인 이메일입니다."
        assert body["details"] == [
            {
                "field": "email",
                "reason": "ALREADY_EXISTS",
                "rejected_value": None,
            }
        ]
        assert "trace_id" in body
        assert response.headers.get_list("cache-control") == ["no-store"]

    @pytest.mark.parametrize(
        "signup_data",
        [
            {"email": "missing-name@example.com", "password": "Password123!"},
            {"email": "missing-password@example.com", "name": "누락테스터"},
            {"password": "Password123!", "name": "누락테스터"},
        ],
    )
    async def test_signup_rejects_missing_required_mvp_field(self, signup_data: dict[str, str]):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_signup_stores_selected_consents_as_granted(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        mark_signup_email_verified,
    ) -> None:
        monkeypatch.setattr(config, "OCR_CONSENT_POLICY_VERSION", "ocr-consent.v1")
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(
                client,
                email=email,
                consents=[
                    OCR_CONSENT,
                    GUIDE_CONSENT,
                ],
                mark_signup_email_verified=mark_signup_email_verified,
            )

        assert response.status_code == status.HTTP_201_CREATED
        rows = await _consents_for_email(db_session, email=email)
        assert {(row.purpose, row.status, row.policy_version) for row in rows} == {
            (ConsentPurpose.OCR, ConsentStatus.GRANTED, "ocr-consent.v1"),
            (ConsentPurpose.GUIDE, ConsentStatus.GRANTED, "guide-consent.v1"),
        }
        assert all(row.granted_at is not None for row in rows)
        assert all(row.withdrawn_at is None for row in rows)

    async def test_signup_does_not_store_unselected_consents(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        mark_signup_email_verified,
    ) -> None:
        monkeypatch.setattr(config, "OCR_CONSENT_POLICY_VERSION", "ocr-consent.v1")
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(
                client,
                email=email,
                mark_signup_email_verified=mark_signup_email_verified,
            )

        assert response.status_code == status.HTTP_201_CREATED
        assert await _consents_for_email(db_session, email=email) == []

    async def test_signup_accepts_empty_consent_list(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        mark_signup_email_verified,
    ) -> None:
        monkeypatch.setattr(config, "OCR_CONSENT_POLICY_VERSION", "ocr-consent.v1")
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(
                client,
                email=email,
                consents=[],
                mark_signup_email_verified=mark_signup_email_verified,
            )

        assert response.status_code == status.HTTP_201_CREATED
        assert await _consents_for_email(db_session, email=email) == []

    async def test_signup_can_store_all_consent_purposes(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        mark_signup_email_verified,
    ) -> None:
        monkeypatch.setattr(config, "OCR_CONSENT_POLICY_VERSION", "ocr-consent.v1")
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(
                client,
                email=email,
                consents=[
                    OCR_CONSENT,
                    GUIDE_CONSENT,
                    CHAT_CONSENT,
                    NOTIFICATION_CONSENT,
                ],
                mark_signup_email_verified=mark_signup_email_verified,
            )

        assert response.status_code == status.HTTP_201_CREATED
        rows = await _consents_for_email(db_session, email=email)
        assert {row.purpose for row in rows} == set(ConsentPurpose)
        assert all(row.status == ConsentStatus.GRANTED for row in rows)

    async def test_signup_rejects_invalid_consent_purpose(self) -> None:
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(
                client,
                email=email,
                consents=[{"purpose": "LOCATION"}],
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_signup_rejects_duplicate_consent_purpose(self) -> None:
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(
                client,
                email=email,
                consents=[
                    GUIDE_CONSENT,
                    GUIDE_CONSENT,
                ],
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.headers.get_list("cache-control") == ["no-store"]

    @pytest.mark.parametrize(
        "consent",
        [
            {"purpose": "GUIDE", "policy_version": "guide-consent.v1"},
            {"purpose": "GUIDE", "status": "GRANTED"},
        ],
    )
    async def test_signup_rejects_client_supplied_consent_internals(
        self,
        db_session: AsyncSession,
        consent: dict[str, str],
    ) -> None:
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(client, email=email, consents=[consent])

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert await db_session.scalar(select(User.id).where(User.email == email.lower())) is None
        assert await _consents_for_email(db_session, email=email) == []

    async def test_signup_rejects_consent_when_policy_is_unavailable(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        mark_signup_email_verified,
    ) -> None:
        monkeypatch.setattr(config, "OCR_CONSENT_POLICY_VERSION", "")
        email = _email()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await _signup(
                client,
                email=email,
                consents=[OCR_CONSENT],
                mark_signup_email_verified=mark_signup_email_verified,
            )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["code"] == "CONSENT_POLICY_UNAVAILABLE"
        assert await db_session.scalar(select(User.id).where(User.email == email.lower())) is None

    async def test_signup_rolls_back_user_when_consent_storage_fails(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        mark_signup_email_verified,
    ) -> None:
        monkeypatch.setattr(config, "OCR_CONSENT_POLICY_VERSION", "ocr-consent.v1")
        failing_repository = AsyncMock()
        failing_repository.set_status.side_effect = RuntimeError("synthetic consent storage failure")

        def override_get_user_consent_repository():
            return failing_repository

        fastapi_app.dependency_overrides[get_user_consent_repository] = override_get_user_consent_repository
        email = _email()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                response = await _signup(
                    client,
                    email=email,
                    consents=[OCR_CONSENT],
                    mark_signup_email_verified=mark_signup_email_verified,
                )
        finally:
            fastapi_app.dependency_overrides.pop(get_user_consent_repository, None)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert await db_session.scalar(select(User.id).where(User.email == email.lower())) is None
        assert await _consents_for_email(db_session, email=email) == []

    async def test_signup_rejects_profile_fields_in_mvp_signup(self):
        signup_data = {
            "email": "profile-fields@example.com",
            "password": "Password123!",
            "name": "추가정보테스터",
            "gender": "MALE",
            "birth_date": "1990-01-01",
            "phone_number": "01012345678",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.headers.get_list("cache-control") == ["no-store"]
