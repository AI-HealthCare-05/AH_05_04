from httpx import ASGITransport, AsyncClient
from starlette import status

from app.main import app


class TestJWTTokenRefreshAPI:
    async def test_token_refresh_success(self):
        # 사용자 등록 및 로그인하여 리프레시 토큰 획득
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

            # 쿠키에서 refresh_token 추출
            set_cookie = login_response.headers.get("set-cookie")
            refresh_token = ""
            if set_cookie:
                import re

                match = re.search(r"refresh_token=([^;]+)", set_cookie)
                if match:
                    refresh_token = match.group(1)

            # 토큰 갱신 시도
            client.cookies["refresh_token"] = refresh_token
            response = await client.get("/api/v1/auth/token/refresh")
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.json()

    async def test_token_refresh_missing_token(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/auth/token/refresh")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["code"] == "UNAUTHORIZED"
        assert body["message"] == "로그인이 필요합니다."
        assert "trace_id" in body

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

            # Access Token을 Refresh Token 자리에 그대로 사용
            client.cookies["refresh_token"] = access_token
            response = await client.get("/api/v1/auth/token/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "INVALID_TOKEN"
