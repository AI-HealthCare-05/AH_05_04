from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core.jwt.tokens import AccessToken, RefreshToken
from app.main import app


class TestUserMeApis:
    async def test_get_user_me_success(self):
        # 사용자 등록 및 로그인
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

            # 내 정보 조회
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == email
        assert response.json()["name"] == "내정보테스터"
        assert response.json()["gender"] is None
        assert response.json()["birthday"] is None
        assert response.json()["phone_number"] is None

    async def test_update_user_me_success(self):
        # 사용자 등록 및 로그인
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

            # 내 정보 수정
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.patch("/api/v1/users/me", json=update_data, headers=headers)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["name"] == "수정후"
        assert response.json()["gender"] is None
        assert response.json()["birthday"] is None
        assert response.json()["phone_number"] is None

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

    async def test_get_user_me_unauthorized(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/users/me")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_get_user_me_rejects_invalid_access_token(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": "Bearer invalid-access-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "INVALID_TOKEN"
        assert response.headers["www-authenticate"] == "Bearer"

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
