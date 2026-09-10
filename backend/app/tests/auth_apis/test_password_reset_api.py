from datetime import datetime, timedelta
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core import config
from app.core.utils.security import generate_password_reset_token, hash_password_reset_token
from app.main import app
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.user_repository import UserRepository


class TestPasswordResetRequestAPI:
    async def test_request_for_existing_account_returns_reset_token_in_local_env(self):
        email = f"reset-request-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "재설정요청테스터"},
            )
            response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["detail"]
        assert body["reset_token"]

    async def test_request_for_unknown_account_returns_same_response_shape(self):
        """PD-206 결정 3: 계정 존재 여부를 노출하지 않기 위해, 존재하지 않는 계정도
        존재하는 계정과 같은 성공 응답을 반환해야 한다(단 reset_token은 비어 있다)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/password-reset/request",
                json={"email": f"unknown-{uuid4().hex[:10]}@example.com"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["detail"]
        assert body["reset_token"] is None

    async def test_request_within_cooldown_does_not_issue_a_new_token(self):
        email = f"reset-cooldown-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "쿨다운테스터"},
            )
            first_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
            second_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})

        assert first_response.json()["reset_token"]
        assert second_response.status_code == status.HTTP_200_OK
        assert second_response.json()["reset_token"] is None


class TestPasswordResetConfirmAPI:
    async def test_confirm_with_valid_token_changes_password_and_requires_relogin(self):
        email = f"reset-confirm-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "재설정확정테스터"},
            )
            request_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
            reset_token = request_response.json()["reset_token"]

            confirm_response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": reset_token, "new_password": "NewPassword456!"},
            )

            old_password_login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            new_password_login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "NewPassword456!"}
            )

        assert confirm_response.status_code == status.HTTP_200_OK
        # 결정 3: 재설정 성공 응답은 access/refresh token을 발급하지 않는다.
        assert set(confirm_response.json().keys()) == {"detail"}
        assert old_password_login.status_code == status.HTTP_401_UNAUTHORIZED
        assert new_password_login.status_code == status.HTTP_200_OK

    async def test_confirm_invalidates_sessions_issued_before_reset(self):
        email = f"reset-session-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "세션무효화테스터"},
            )
            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            access_token = login_response.json()["access_token"]

            request_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
            await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": request_response.json()["reset_token"], "new_password": "NewPassword456!"},
            )

            me_response = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert me_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert me_response.json()["code"] == "INVALID_TOKEN"

    async def test_confirm_rejects_reused_token(self):
        email = f"reset-reuse-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "토큰재사용테스터"},
            )
            request_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
            reset_token = request_response.json()["reset_token"]

            await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": reset_token, "new_password": "NewPassword456!"},
            )
            second_confirm_response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": reset_token, "new_password": "AnotherPassword789!"},
            )

        assert second_confirm_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = second_confirm_response.json()
        assert body["code"] == "VALIDATION_FAILED"
        assert body["details"] == [{"field": "token", "reason": "RESET_TOKEN_INVALID", "rejected_value": None}]

    async def test_confirm_rejects_unknown_token(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": "not-a-real-token", "new_password": "NewPassword456!"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = response.json()
        assert body["code"] == "VALIDATION_FAILED"
        assert body["details"] == [{"field": "token", "reason": "RESET_TOKEN_INVALID", "rejected_value": None}]

    async def test_confirm_rejects_weak_new_password(self):
        email = f"reset-weak-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "약한비번테스터"},
            )
            request_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})

            response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": request_response.json()["reset_token"], "new_password": "weak"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = response.json()
        assert body["code"] == "VALIDATION_FAILED"
        assert body["details"] == [
            {"field": "new_password", "reason": "PASSWORD_POLICY_VIOLATION", "rejected_value": None}
        ]

    async def test_confirm_rejects_new_password_over_72_chars(self):
        """PR #404 리뷰(권가빈): `PasswordResetConfirmRequest`는 DTO 레벨 길이 제약이
        없어, 회원가입과 달리 72자 초과 비밀번호가 `validate_password()`를 그대로
        통과해 `hash_password()`까지 도달했다. 73자 경계에서 거부되는지 확인한다."""
        email = f"reset-toolong-{uuid4().hex[:8]}@example.com"
        too_long_password = "Aa1!" + "a" * 69  # 73자, 모든 문자 종류 포함
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "긴비번테스터"},
            )
            request_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})

            response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": request_response.json()["reset_token"], "new_password": too_long_password},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = response.json()
        assert body["code"] == "VALIDATION_FAILED"
        assert body["details"] == [
            {"field": "new_password", "reason": "PASSWORD_POLICY_VIOLATION", "rejected_value": None}
        ]

    async def test_confirm_accepts_new_password_at_72_char_boundary(self):
        email = f"reset-72char-{uuid4().hex[:8]}@example.com"
        boundary_password = "Aa1!" + "a" * 68  # 정확히 72자
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "경계비번테스터"},
            )
            request_response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})

            response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": request_response.json()["reset_token"], "new_password": boundary_password},
            )

        assert response.status_code == status.HTTP_200_OK

    async def test_confirm_rejects_expired_token(self, db_session):
        email = f"reset-expired-{uuid4().hex[:8]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "만료토큰테스터"},
            )

        user = await UserRepository(db_session).get_user_by_email(email)
        raw_token = generate_password_reset_token()
        await PasswordResetRepository(db_session).create_token(
            user_id=user.id,
            token_hash=hash_password_reset_token(raw_token),
            expires_at=datetime.now(config.TIMEZONE) - timedelta(minutes=1),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": raw_token, "new_password": "NewPassword456!"},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = response.json()
        assert body["code"] == "VALIDATION_FAILED"
        assert body["details"] == [{"field": "token", "reason": "RESET_TOKEN_INVALID", "rejected_value": None}]

    async def test_confirm_consumes_all_other_valid_tokens_for_the_same_user(self, db_session):
        """PD-206 결정 3: 같은 사용자가 재설정을 여러 번 요청해 유효한 token이 동시에
        여러 개 존재하면, 하나를 소비할 때 나머지도 함께 소비해야 한다 — 그렇지 않으면
        다른 유효 token으로 비밀번호를 다시 바꿀 수 있다(쿨다운 때문에 API로는 유효
        token 2개를 동시에 받을 수 없어, repository로 직접 그 상황을 재현한다)."""
        email = f"reset-multi-{uuid4().hex[:8]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "복수토큰테스터"},
            )

        user = await UserRepository(db_session).get_user_by_email(email)
        password_reset_repo = PasswordResetRepository(db_session)
        first_raw_token = generate_password_reset_token()
        second_raw_token = generate_password_reset_token()
        expires_at = datetime.now(config.TIMEZONE) + timedelta(minutes=config.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
        await password_reset_repo.create_token(
            user_id=user.id, token_hash=hash_password_reset_token(first_raw_token), expires_at=expires_at
        )
        await password_reset_repo.create_token(
            user_id=user.id, token_hash=hash_password_reset_token(second_raw_token), expires_at=expires_at
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first_confirm_response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": first_raw_token, "new_password": "NewPassword456!"},
            )
            second_confirm_response = await client.post(
                "/api/v1/auth/password-reset/confirm",
                json={"token": second_raw_token, "new_password": "AnotherPassword789!"},
            )

        assert first_confirm_response.status_code == status.HTTP_200_OK
        assert second_confirm_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        body = second_confirm_response.json()
        assert body["code"] == "VALIDATION_FAILED"
        assert body["details"] == [{"field": "token", "reason": "RESET_TOKEN_INVALID", "rejected_value": None}]
