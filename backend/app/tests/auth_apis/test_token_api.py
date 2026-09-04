import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core.jwt.tokens import AccessToken, RefreshToken
from app.dependencies.services import get_user_repository
from app.main import app, fastapi_app
from app.models.users import AccountStatus, User


def extract_refresh_token(response) -> str:
    set_cookie = response.headers.get("set-cookie", "")
    match = re.search(r"refresh_token=([^;]+)", set_cookie)
    assert match is not None
    return match.group(1)


def build_expired_access_token(live_token: AccessToken) -> AccessToken:
    """`live_token`과 같은 사용자·`token_version`이지만 이미 만료된 access token을 만듭니다.

    `Token.set_exp()`는 `calendar.timegm(dt.timetuple())`으로 시각대가 있는 `dt`를 그 벽시계
    값 그대로 UTC로 오인해서 계산합니다(`TIMEZONE=Asia/Seoul`이면 실제 만료 시각이 항상 의도한
    값보다 9시간 뒤로 계산되는 별개의 기존 버그 — 이번 리뷰와 무관하게 별도 보고 예정). 그래서
    `set_exp(from_time=... - timedelta(...))`로는 짧은 시간 전으로는 진짜 과거 만료를 만들 수
    없어, `payload["exp"]`에 실제 UTC epoch을 직접 대입해 우회합니다."""
    expired = AccessToken()
    expired["user_id"] = live_token["user_id"]
    expired["token_version"] = live_token["token_version"]
    expired.payload["exp"] = int(datetime.now(UTC).timestamp()) - 600
    return expired


class TestJWTTokenRefreshAPI:
    async def test_token_refresh_success(self):
        signup_data = {
            "email": "refresh@example.com",
            "password": "Password123!",
            "name": "리프레시테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            login_response = await client.post(
                "/api/v1/auth/login", json={"email": "refresh@example.com", "password": "Password123!"}
            )

            refresh_token = extract_refresh_token(login_response)
            client.cookies["refresh_token"] = refresh_token
            response = await client.get("/api/v1/auth/token/refresh")
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.json()
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_token_refresh_missing_token(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/auth/token/refresh")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["code"] == "UNAUTHORIZED"
        assert body["message"] == "로그인이 필요합니다."
        assert "trace_id" in body
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_token_refresh_rejects_access_token_used_as_refresh_token(self):
        signup_data = {
            "email": "type-confusion@example.com",
            "password": "Password123!",
            "name": "타입혼동테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": "type-confusion@example.com", "password": "Password123!"},
            )
            access_token = login_response.json()["access_token"]

            client.cookies["refresh_token"] = access_token
            response = await client.get("/api/v1/auth/token/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "INVALID_TOKEN"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_token_refresh_rejects_token_after_user_token_version_changes(self):
        user = User(
            id=uuid4(),
            email="refresh-version@example.com",
            hashed_password="hashed",
            name="리프레시버전테스터",
            account_status=AccountStatus.ACTIVE,
            is_active=True,
            token_version=0,
        )
        refresh_token = RefreshToken.for_user(user)
        user.token_version = 1
        repository = AsyncMock()
        repository.get_user.return_value = user
        fastapi_app.dependency_overrides[get_user_repository] = lambda: repository

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                client.cookies["refresh_token"] = str(refresh_token)
                response = await client.get("/api/v1/auth/token/refresh")
        finally:
            fastapi_app.dependency_overrides.pop(get_user_repository, None)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "INVALID_TOKEN"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_token_refresh_rejects_inactive_account_even_with_unexpired_refresh_token(self):
        user = User(
            id=uuid4(),
            email="refresh-inactive@example.com",
            hashed_password="hashed",
            name="리프레시비활성테스터",
            account_status=AccountStatus.ACTIVE,
            is_active=True,
            token_version=0,
        )
        refresh_token = RefreshToken.for_user(user)
        user.account_status = AccountStatus.WITHDRAWAL_REQUESTED
        user.is_active = False
        repository = AsyncMock()
        repository.get_user.return_value = user
        fastapi_app.dependency_overrides[get_user_repository] = lambda: repository

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                client.cookies["refresh_token"] = str(refresh_token)
                response = await client.get("/api/v1/auth/token/refresh")
        finally:
            fastapi_app.dependency_overrides.pop(get_user_repository, None)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "INVALID_TOKEN"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers.get_list("cache-control") == ["no-store"]


class TestLogoutAPI:
    async def test_logout_increments_token_version_and_deletes_refresh_cookie(self):
        email = f"logout-{uuid4().hex[:12]}@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "로그아웃테스터",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )
            access_token = login_response.json()["access_token"]
            refresh_token = extract_refresh_token(login_response)

            logout_response = await client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            me_response = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            client.cookies["refresh_token"] = refresh_token
            refresh_response = await client.get("/api/v1/auth/token/refresh")

        assert logout_response.status_code == status.HTTP_200_OK
        assert logout_response.json()["detail"] == "로그아웃되었습니다."
        assert logout_response.headers.get_list("cache-control") == ["no-store"]
        assert any(
            "refresh_token=" in header and "Max-Age=0" in header
            for header in logout_response.headers.get_list("set-cookie")
        )

        assert me_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert me_response.json()["code"] == "INVALID_TOKEN"
        assert me_response.headers.get_list("cache-control") == ["no-store"]

        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert refresh_response.json()["code"] == "INVALID_TOKEN"
        assert refresh_response.headers["www-authenticate"] == "Bearer"
        assert refresh_response.headers.get_list("cache-control") == ["no-store"]

    async def test_logout_requires_access_token(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/logout")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "UNAUTHORIZED"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers.get_list("cache-control") == ["no-store"]
        # PD-206 리뷰: 인증 결과와 무관하게 refresh_token 쿠키는 항상 삭제되어야 합니다.
        assert any(
            "refresh_token=" in header and "Max-Age=0" in header for header in response.headers.get_list("set-cookie")
        )

    async def test_logout_falls_back_to_refresh_token_when_access_token_expired(self):
        """PD-206 리뷰: access token이 만료된 상태로 로그아웃해도 유효한 refresh token으로
        신원을 확인해 token_version을 증가시켜야 합니다 — 그렇지 않으면 로그아웃 후에도
        남아 있는 refresh token으로 세션이 재발급될 수 있습니다."""
        email = f"logout-expired-{uuid4().hex[:12]}@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "만료토큰로그아웃테스터",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )
            refresh_token = extract_refresh_token(login_response)

            live_access_token = AccessToken(token=login_response.json()["access_token"])
            expired_access_token = build_expired_access_token(live_access_token)

            # `base_url="http://test"`와 `COOKIE_DOMAIN=localhost`(테스트 환경)가 달라 httpx가
            # login 응답의 Set-Cookie를 자동으로 재전송하지 않으므로 명시적으로 심어줍니다.
            client.cookies["refresh_token"] = refresh_token
            logout_response = await client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {expired_access_token}"},
            )

            refresh_response = await client.get("/api/v1/auth/token/refresh")

        assert logout_response.status_code == status.HTTP_200_OK
        assert logout_response.json()["detail"] == "로그아웃되었습니다."
        assert logout_response.headers.get_list("cache-control") == ["no-store"]
        assert any(
            "refresh_token=" in header and "Max-Age=0" in header
            for header in logout_response.headers.get_list("set-cookie")
        )

        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert refresh_response.json()["code"] == "INVALID_TOKEN"
        assert refresh_response.headers.get_list("cache-control") == ["no-store"]

    async def test_logout_rejects_expired_access_token_without_refresh_token_fallback(self):
        """만료된 access token만 있고 refresh token이 없으면 fallback할 근거가 없으므로
        원래의 `EXPIRED_TOKEN` 오류를 그대로 반환해야 합니다."""
        email = f"logout-noref-{uuid4().hex[:12]}@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "만료전용테스터",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )

            live_access_token = AccessToken(token=login_response.json()["access_token"])
            expired_access_token = build_expired_access_token(live_access_token)

            # refresh_token 쿠키를 전혀 심지 않아 "refresh token 없음" 상태를 재현합니다.
            logout_response = await client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {expired_access_token}"},
            )

        assert logout_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert logout_response.json()["code"] == "EXPIRED_TOKEN"
        assert logout_response.headers["www-authenticate"] == "Bearer"
        assert logout_response.headers.get_list("cache-control") == ["no-store"]
        assert any(
            "refresh_token=" in header and "Max-Age=0" in header
            for header in logout_response.headers.get_list("set-cookie")
        )
