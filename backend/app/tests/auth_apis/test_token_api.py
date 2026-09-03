import re
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core.jwt.tokens import RefreshToken
from app.dependencies.services import get_user_repository
from app.main import app, fastapi_app
from app.models.users import AccountStatus, User


def extract_refresh_token(response) -> str:
    set_cookie = response.headers.get("set-cookie", "")
    match = re.search(r"refresh_token=([^;]+)", set_cookie)
    assert match is not None
    return match.group(1)


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
