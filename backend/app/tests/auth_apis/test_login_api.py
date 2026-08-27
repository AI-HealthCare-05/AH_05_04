from datetime import date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core.utils.security import hash_password
from app.dependencies.services import get_auth_service
from app.main import app, fastapi_app
from app.models.users import Gender, User
from app.services.auth import AuthService


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

    async def test_login_invalid_credentials(self):
        login_data = {"email": "nonexistent@example.com", "password": "WrongPassword123!"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/login", json=login_data)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "UNAUTHORIZED"
        assert response.headers["www-authenticate"] == "Bearer"

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
