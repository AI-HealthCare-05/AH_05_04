import re
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core import config
from app.core.jwt.tokens import AccessToken
from app.core.utils.security import hash_password
from app.dependencies.services import get_auth_service
from app.main import app, fastapi_app
from app.models.users import Gender, User
from app.services.auth import AuthService


def _extract_refresh_cookie_expires(response) -> datetime:
    set_cookie = response.headers.get("set-cookie", "")
    match = re.search(r"[Ee]xpires=([^;]+)", set_cookie)
    assert match is not None
    return parsedate_to_datetime(match.group(1))


class TestLoginAPI:
    async def test_login_success(self):
        # 먼저 사용자 등록
        signup_data = {
            "email": "login_test@example.com",
            "password": "Password123!",
            "name": "로그인테스터",
        }
        login_data = {"email": "login_test@example.com", "password": "Password123!"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            # 로그인 시도
            response = await client.post("/api/v1/auth/login", json=login_data)
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.json()
        assert response.headers.get_list("cache-control") == ["no-store"]
        # 쿠키 검증 대신 응답 헤더 확인
        assert any("refresh_token" in header for header in response.headers.get_list("set-cookie"))

    async def test_login_accepts_case_variant_email(self) -> None:
        """저장된 이메일과 대소문자가 달라도 같은 계정으로 로그인합니다."""
        signup_data = {
            "email": "case-login@example.com",
            "password": "Password123!",
            "name": "이메일정규화테스트",
        }
        login_data = {
            "email": "CASE-LOGIN@EXAMPLE.COM",
            "password": "Password123!",
        }

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            signup_response = await client.post(
                "/api/v1/auth/signup",
                json=signup_data,
            )
            response = await client.post(
                "/api/v1/auth/login",
                json=login_data,
            )

        assert signup_response.status_code == status.HTTP_201_CREATED
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.json()
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_login_invalid_credentials(self):
        login_data = {"email": "nonexistent@example.com", "password": "WrongPassword123!"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/login", json=login_data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "UNAUTHORIZED"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_login_rejects_email_over_max_length(self):
        login_data = {
            "email": f"{'a' * 32}@example.com",
            "password": "Password123!",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/login", json=login_data)

        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_FAILED"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_login_rejects_wrong_password_for_existing_user(self):
        signup_data = {
            "email": "wrong_password@example.com",
            "password": "Password123!",
            "name": "비밀번호테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            response = await client.post(
                "/api/v1/auth/login",
                json={"email": "wrong_password@example.com", "password": "WrongPassword123!"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "UNAUTHORIZED"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_login_rejects_inactive_account(self):
        inactive_user = User(
            id=uuid4(),
            email="inactive@example.com",
            hashed_password=hash_password("Password123!"),
            name="비활성테스터",
            gender=Gender.FEMALE,
            birthday=date(1990, 1, 1),
            phone_number="01012349876",
            is_active=False,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        repository = AsyncMock()
        repository.get_user_by_email.return_value = inactive_user
        fastapi_app.dependency_overrides[get_auth_service] = lambda: AuthService(repository)

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "inactive@example.com", "password": "Password123!"},
                )
        finally:
            fastapi_app.dependency_overrides.pop(get_auth_service, None)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["code"] == "FORBIDDEN"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_login_refresh_cookie_expires_matches_refresh_token_not_access_token(self) -> None:
        """4차 리뷰 지적: `refresh_token` 쿠키의 `Expires`가 `access_token.exp`(기본 1시간)를
        쓰고 있어, refresh token JWT 자체의 실제 수명(설정된 14일)과 쿠키 수명이 어긋났다.

        고치는 과정에서 더 근본적인 문제도 함께 드러났다 — Python `http.cookies`는
        `expires=`에 정수를 그대로 넘기면 절대 epoch이 아니라 "지금부터 그 초만큼 후"로
        해석한다(`http.cookies._getdate`). `exp` 클레임은 둘 다 큰 절대 epoch 값이라, 단순히
        `access_token.exp`를 `refresh_token.exp`로 바꾸기만 하면 실제 쿠키 수명이 1시간에서
        14일이 아니라 ~55년에서 ~56년으로 바뀔 뿐이었다. 그래서 실제 `Set-Cookie`의 `Expires`를
        HTTP-date로 직접 파싱해, refresh token의 실제 `exp`와 일치하고 access token의 1시간짜리
        `exp`나 "int를 상대 offset으로 오인한" 값이 아닌지 모두 검증한다."""
        email = f"cookie-expires-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "쿠키만료테스터"},
            )
            before_login = datetime.now(UTC)
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )
            after_login = datetime.now(UTC)

        access_token_exp = datetime.fromtimestamp(
            AccessToken(token=response.json()["access_token"]).payload["exp"], tz=UTC
        )
        cookie_expires = _extract_refresh_cookie_expires(response)

        expected_earliest = before_login + timedelta(minutes=config.REFRESH_TOKEN_EXPIRE_MINUTES) - timedelta(seconds=2)
        expected_latest = after_login + timedelta(minutes=config.REFRESH_TOKEN_EXPIRE_MINUTES) + timedelta(seconds=2)

        assert expected_earliest <= cookie_expires <= expected_latest
        # access token exp(1시간 후)와는 확실히 달라야 하며, "int를 상대 offset으로 오인"하는
        # 회귀가 재발하면 이 값이 수십 년 뒤로 튀므로 30일 상한으로 함께 막는다.
        assert cookie_expires != access_token_exp
        assert cookie_expires < before_login + timedelta(days=30)
