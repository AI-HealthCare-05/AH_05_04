from datetime import UTC, date, datetime, timedelta
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
from app.tests.helpers.auth import signup_verified_user


class TestUserMeApis:
    async def test_get_user_me_success(self):
        email = "me@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "내정보테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await signup_verified_user(client, signup_data)

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
            await signup_verified_user(client, signup_data)

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

    async def test_update_user_me_updates_email_with_normalization(self):
        email = "update_email_me@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "이메일수정전",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await signup_verified_user(client, signup_data)

            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]

            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.patch(
                "/api/v1/users/me",
                json={"email": "UPDATED_EMAIL_ME@EXAMPLE.COM"},
                headers=headers,
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == "updated_email_me@example.com"
        assert response.headers.get_list("cache-control") == ["no-store"]

    async def test_update_user_me_accepts_basic_profile_fields(self):
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
            await signup_verified_user(client, signup_data)

            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]

            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.patch("/api/v1/users/me", json=update_data, headers=headers)
            get_response = await client.get("/api/v1/users/me", headers=headers)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["gender"] == "MALE"
        assert response.json()["birthday"] == "1990-10-10"
        assert response.json()["phone_number"] == "01077778888"
        assert response.headers.get_list("cache-control") == ["no-store"]
        assert get_response.status_code == status.HTTP_200_OK
        assert get_response.json()["gender"] == "MALE"
        assert get_response.json()["birthday"] == "1990-10-10"
        assert get_response.json()["phone_number"] == "01077778888"

    async def test_update_user_me_rejects_duplicate_phone_number(self):
        unique_suffix = uuid4().hex[:12]
        first_email = f"phone-owner-{unique_suffix}@example.com"
        second_email = f"phone-conflict-{unique_suffix}@example.com"
        phone_number = "01012345678"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await signup_verified_user(
                client,
                {
                    "email": first_email,
                    "password": "Password123!",
                    "name": "번호소유자",
                },
            )
            await signup_verified_user(
                client,
                {
                    "email": second_email,
                    "password": "Password123!",
                    "name": "번호충돌자",
                },
            )

            first_login = await client.post(
                "/api/v1/auth/login",
                json={"email": first_email, "password": "Password123!"},
            )
            second_login = await client.post(
                "/api/v1/auth/login",
                json={"email": second_email, "password": "Password123!"},
            )
            first_headers = {"Authorization": f"Bearer {first_login.json()['access_token']}"}
            second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}

            first_response = await client.patch(
                "/api/v1/users/me",
                json={"phone_number": phone_number},
                headers=first_headers,
            )
            second_response = await client.patch(
                "/api/v1/users/me",
                json={"phone_number": phone_number},
                headers=second_headers,
            )

        assert first_response.status_code == status.HTTP_200_OK
        assert second_response.status_code == status.HTTP_409_CONFLICT
        assert second_response.json()["code"] == "CONFLICT"
        assert second_response.json()["details"] == [
            {
                "field": "phone_number",
                "reason": "ALREADY_EXISTS",
                "rejected_value": None,
            }
        ]
        assert second_response.headers.get_list("cache-control") == ["no-store"]

    async def test_update_user_me_preserves_profile_fields_when_omitted(self):
        email = "omit_profile_fields@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "수정전",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await signup_verified_user(client, signup_data)

            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {access_token}"}

            await client.patch(
                "/api/v1/users/me",
                json={
                    "gender": "FEMALE",
                    "birthday": "1991-11-11",
                    "phone_number": "01099998888",
                },
                headers=headers,
            )
            response = await client.patch("/api/v1/users/me", json={"name": "수정후"}, headers=headers)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "수정후"
        assert response.json()["gender"] == "FEMALE"
        assert response.json()["birthday"] == "1991-11-11"
        assert response.json()["phone_number"] == "01099998888"

    async def test_update_user_me_clears_basic_profile_fields_with_null(self):
        email = "clear_profile_fields@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "초기화전",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await signup_verified_user(client, signup_data)

            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {access_token}"}

            await client.patch(
                "/api/v1/users/me",
                json={
                    "gender": "MALE",
                    "birthday": "1990-10-10",
                    "phone_number": "01077778888",
                },
                headers=headers,
            )
            response = await client.patch(
                "/api/v1/users/me",
                json={"gender": None, "birthday": None, "phone_number": None},
                headers=headers,
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["gender"] is None
        assert response.json()["birthday"] is None
        assert response.json()["phone_number"] is None
        assert response.headers.get_list("cache-control") == ["no-store"]

    @pytest.mark.parametrize(
        "update_data",
        [
            {"phone_number": ""},
            {"phone_number": "010-7777-8888"},
            {"phone_number": "0101234567"},
            {"phone_number": "010123456789"},
            {"phone_number": "01112345678"},
            {"birthday": (date.today() + timedelta(days=1)).isoformat()},
            {"gender": "UNKNOWN"},
        ],
    )
    async def test_update_user_me_rejects_invalid_basic_profile_fields(self, update_data):
        email = f"invalid-profile-{uuid4().hex[:12]}@example.com"
        signup_data = {
            "email": email,
            "password": "Password123!",
            "name": "검증테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await signup_verified_user(client, signup_data)

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
