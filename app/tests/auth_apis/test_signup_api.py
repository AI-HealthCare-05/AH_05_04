from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette import status

from app.dependencies.services import get_user_repository
from app.main import app, fastapi_app
from app.repositories.user_repository import (
    DuplicateUserField,
    DuplicateUserFieldError,
    UserRepository,
)


class TestSignupAPI:
    async def test_signup_success(self):
        signup_data = {
            "email": "test@example.com",
            "password": "Password123!",
            "name": "테스터",
            "gender": "MALE",
            "birth_date": "1990-01-01",
            "phone_number": "01012345678",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"detail": "회원가입이 성공적으로 완료되었습니다."}

    async def test_signup_invalid_email(self):
        signup_data = {
            "email": "invalid-email",
            "password": "password123!",
            "name": "테스터",
            "gender": "MALE",
            "birth_date": "1990-01-01",
            "phone_number": "01012345678",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/auth/signup", json=signup_data)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    @pytest.mark.parametrize(
        ("duplicate_field", "expected_detail"),
        [
            ("email", "이미 사용중인 이메일입니다."),
            (
                "phone_number",
                "이미 사용중인 휴대폰 번호입니다.",
            ),
        ],
    )
    async def test_signup_returns_conflict_for_concurrent_duplicate(
        self,
        duplicate_field: DuplicateUserField,
        expected_detail: str,
    ):
        repository = AsyncMock(spec=UserRepository)
        repository.exists_by_email.return_value = False
        repository.exists_by_phone_number.return_value = False
        repository.create_user.side_effect = DuplicateUserFieldError(duplicate_field)

        def override_get_user_repository():
            return repository

        fastapi_app.dependency_overrides[get_user_repository] = override_get_user_repository

        signup_data = {
            "email": "race@example.com",
            "password": "Password123!",
            "name": "동시가입테스트",
            "gender": "MALE",
            "birth_date": "1990-01-01",
            "phone_number": "01099998888",
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
        assert body["code"] == "HTTP_ERROR"
        assert body["message"] == expected_detail
        assert body["details"] == []
        assert "trace_id" in body
