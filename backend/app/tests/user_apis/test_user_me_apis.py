from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core.errors import ApiError
from app.core.jwt.tokens import AccessToken, RefreshToken
from app.dependencies.security import get_request_user
from app.main import app
from app.models.users import AccountStatus, User


class TestUserMeApis:
    async def test_get_user_me_success(self):
        email = "me@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "내정보테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]
            verified_access_token = AccessToken(token=access_token)

            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == status.HTTP_200_OK
        assert verified_access_token.payload["token_version"] == 0
        assert response.json()["email"] == email
        assert response.json()["name"] == "내정보테스터"
        assert response.json()["gender"] is None
        assert response.json()["birthday"] is None
        assert response.json()["phone_number"] is None
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_update_user_me_success(self):
        email = "update_me@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "수정전",
        }
        update_data = {
            "name": "수정후",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]

            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.patch("/api/v1/users/me", json=update_data, headers=headers)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "수정후"
        assert response.json()["gender"] is None
        assert response.json()["birthday"] is None
        assert response.json()["phone_number"] is None
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_update_user_me_rejects_post_mvp_profile_fields(self):
        email = "update_profile_fields@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "수정전",
        }
        update_data = {
            "gender": "MALE",
            "birthday": "1990-10-10",
            "phone_number": "01077778888",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]

            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.patch("/api/v1/users/me", json=update_data, headers=headers)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_get_user_me_unauthorized(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/users/me")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_get_user_me_rejects_invalid_access_token(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": "Bearer invalid-access-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "INVALID_TOKEN"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_get_user_me_rejects_expired_access_token(self):
        expired_token = AccessToken()
        expired_token["user_id"] = str(uuid4())
        expired_token.set_exp(
            from_time=datetime.now(UTC) - timedelta(minutes=2),
            lifetime=timedelta(),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {expired_token}"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "EXPIRED_TOKEN"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_get_user_me_rejects_refresh_token_used_as_access_token(self):
        refresh_token = RefreshToken()
        refresh_token["user_id"] = str(uuid4())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {refresh_token}"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "INVALID_TOKEN"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_get_user_me_rejects_token_after_user_token_version_changes(self):
        user = User(
            id=uuid4(),
            email="token-version@example.com",
            hashed_password="hashed",
            name="토큰버전테스터",
            account_status=AccountStatus.ACTIVE,
            is_active=True,
            token_version=0,
        )
        access_token = AccessToken.for_user(user)
        user.token_version = 1
        repository = AsyncMock()
        repository.get_user.return_value = user

        with pytest.raises(ApiError) as exc_info:
            await get_request_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=str(access_token)),
                repository,
            )

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.code == "INVALID_TOKEN"

    async def test_get_user_me_rejects_inactive_account_even_with_unexpired_token(self):
        user = User(
            id=uuid4(),
            email="inactive-token@example.com",
            hashed_password="hashed",
            name="비활성토큰테스터",
            account_status=AccountStatus.ACTIVE,
            is_active=True,
            token_version=0,
        )
        access_token = AccessToken.for_user(user)
        user.account_status = AccountStatus.WITHDRAWAL_REQUESTED
        user.is_active = False
        repository = AsyncMock()
        repository.get_user.return_value = user

        with pytest.raises(ApiError) as exc_info:
            await get_request_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=str(access_token)),
                repository,
            )

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.code == "INVALID_TOKEN"
