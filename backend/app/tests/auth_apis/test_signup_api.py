from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette import status

from app.dependencies.services import get_user_repository
from app.main import app, fastapi_app
from app.repositories.user_repository import DuplicateUserFieldError, UserRepository


class TestSignupAPI:
    async def test_signup_success(self):
        signup_data = {
            "email": "test@example.com",
            "password": "Password123!",
            "name": "테스터",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"detail": "회원가입이 성공적으로 완료되었습니다."}
        assert response.headers.get_list("cache-control") == ["no-store"]

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
