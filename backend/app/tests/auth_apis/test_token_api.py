import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core import config
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

    async def test_token_refresh_issues_access_token_with_correct_lifetime_after_refresh_fix(self):
        """4차 리뷰(refresh token 수명·쿠키 Expires 버그) 수정이 `/token/refresh`가 새로
        발급하는 access token의 수명(`ACCESS_TOKEN_EXPIRE_MINUTES`, 기본 60분)에 실수로
        영향을 주지 않았는지 실제 HTTP 왕복으로 확인합니다."""
        signup_data = {
            "email": "refresh-lifetime@example.com",
            "password": "Password123!",
            "name": "리프레시수명테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)

            login_response = await client.post(
                "/api/v1/auth/login", json={"email": "refresh-lifetime@example.com", "password": "Password123!"}
            )
            client.cookies["refresh_token"] = extract_refresh_token(login_response)

            before_refresh = datetime.now(UTC)
            response = await client.get("/api/v1/auth/token/refresh")
            after_refresh = datetime.now(UTC)

        assert response.status_code == status.HTTP_200_OK
        new_access_token = AccessToken(token=response.json()["access_token"])
        exp = datetime.fromtimestamp(new_access_token.payload["exp"], tz=UTC)

        expected_earliest = (
            before_refresh + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES) - timedelta(seconds=2)
        )
        expected_latest = after_refresh + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES) + timedelta(seconds=2)
        assert expected_earliest <= exp <= expected_latest

    async def test_token_refresh_rotates_refresh_token_cookie(self):
        """#206: `/token/refresh`는 access token뿐 아니라 새 refresh token도 발급해
        cookie를 교체해야 하고, 새로 교체된 refresh token으로도 계속 갱신할 수 있어야 한다."""
        signup_data = {
            "email": "refresh-rotate@example.com",
            "password": "Password123!",
            "name": "로테이션테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)
            login_response = await client.post(
                "/api/v1/auth/login", json={"email": "refresh-rotate@example.com", "password": "Password123!"}
            )
            original_refresh_token = extract_refresh_token(login_response)
            client.cookies["refresh_token"] = original_refresh_token

            first_refresh_response = await client.get("/api/v1/auth/token/refresh")
            rotated_refresh_token = extract_refresh_token(first_refresh_response)
            client.cookies["refresh_token"] = rotated_refresh_token
            second_refresh_response = await client.get("/api/v1/auth/token/refresh")

        assert first_refresh_response.status_code == status.HTTP_200_OK
        assert rotated_refresh_token != original_refresh_token
        assert second_refresh_response.status_code == status.HTTP_200_OK

    async def test_token_refresh_rotated_out_token_does_not_extend_absolute_expiry(self):
        """rotation을 반복해도 refresh token의 절대 만료(`REFRESH_TOKEN_EXPIRE_MINUTES`,
        최초 로그인 기준)는 그대로 유지돼야 한다 — 매번 새로 계산되면 세션이 무기한
        연장된다."""
        signup_data = {
            "email": "refresh-absolute-exp@example.com",
            "password": "Password123!",
            "name": "절대만료테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)
            login_response = await client.post(
                "/api/v1/auth/login", json={"email": "refresh-absolute-exp@example.com", "password": "Password123!"}
            )
            original_exp = RefreshToken(token=extract_refresh_token(login_response)).payload["exp"]

            client.cookies["refresh_token"] = extract_refresh_token(login_response)
            refresh_response = await client.get("/api/v1/auth/token/refresh")
            rotated_exp = RefreshToken(token=extract_refresh_token(refresh_response)).payload["exp"]

        assert rotated_exp == original_exp

    async def test_token_refresh_rejects_expired_refresh_token_even_when_jti_still_active(self):
        """rotation이 절대 만료를 우회하지 않는지 확인한다. `jti`는 여전히 세션의
        `active_jti`와 일치해 재사용 탐지에는 걸리지 않는 상태라도, 시간 자체가
        지난 refresh token은 rotation 로직에 도달하기 전에 기존 JWT 만료 검증에서
        `401 EXPIRED_TOKEN`으로 먼저 걸러져야 한다."""
        signup_data = {
            "email": "refresh-absolute-expired@example.com",
            "password": "Password123!",
            "name": "만료된리프레시테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": "refresh-absolute-expired@example.com", "password": "Password123!"},
            )
            live_refresh_token = RefreshToken(token=extract_refresh_token(login_response))

            # 로그인 시 DB에 기록된 jti는 그대로 두고 exp만 과거로 만든 사본을 제출한다.
            expired_refresh_token = RefreshToken()
            expired_refresh_token.payload["exp"] = int(datetime.now(UTC).timestamp()) - 600
            for claim, value in live_refresh_token.payload.items():
                if claim in ("type", "exp"):
                    continue
                expired_refresh_token[claim] = value

            client.cookies["refresh_token"] = str(expired_refresh_token)
            response = await client.get("/api/v1/auth/token/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "EXPIRED_TOKEN"

    async def test_token_refresh_reuse_of_rotated_out_token_invalidates_entire_session(self):
        """#206: 이미 rotation으로 교체돼 무효해진 refresh token이 다시 제출되면 탈취
        의심 신호로 간주해 그 사용자의 모든 access/refresh token을 강제로 무효화해야
        한다 — 방금 rotation으로 정상 발급된 새 refresh token도 예외가 아니다."""
        signup_data = {
            "email": "refresh-reuse@example.com",
            "password": "Password123!",
            "name": "재사용탐지테스터",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/v1/auth/signup", json=signup_data)
            login_response = await client.post(
                "/api/v1/auth/login", json={"email": "refresh-reuse@example.com", "password": "Password123!"}
            )
            stale_refresh_token = extract_refresh_token(login_response)

            client.cookies["refresh_token"] = stale_refresh_token
            rotate_response = await client.get("/api/v1/auth/token/refresh")
            fresh_refresh_token = extract_refresh_token(rotate_response)

            # 이미 교체된 옛 refresh token을 재사용 시도한다.
            client.cookies["refresh_token"] = stale_refresh_token
            reuse_response = await client.get("/api/v1/auth/token/refresh")

            # 재사용 탐지로 무효화된 뒤에는, 방금 정상 발급됐던 새 refresh token조차 더 이상
            # 쓸 수 없어야 한다(token_version 전체 무효화).
            client.cookies["refresh_token"] = fresh_refresh_token
            after_reuse_response = await client.get("/api/v1/auth/token/refresh")

        assert reuse_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert reuse_response.json()["code"] == "INVALID_TOKEN"
        assert after_reuse_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert after_reuse_response.json()["code"] == "INVALID_TOKEN"

    async def test_token_refresh_two_devices_rotate_independently(self):
        """PR #404 리뷰(권가빈): 로그인마다 `refresh_session` row가 따로 생기므로, 같은
        사용자가 두 기기(브라우저)에서 각각 로그인해도 한쪽의 rotation이 다른 쪽의
        `active_jti`를 덮어써 서로의 다음 refresh를 "재사용"으로 오판하면 안 된다 — 재설계
        전 `User.active_refresh_jti` 단일 컬럼 구조에서는 실제로 이 문제가 있었다."""
        email = f"two-devices-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_a:
            await client_a.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "멀티디바이스테스터"},
            )

        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_a,
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b,
        ):
            login_a = await client_a.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            login_b = await client_b.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            client_a.cookies["refresh_token"] = extract_refresh_token(login_a)
            client_b.cookies["refresh_token"] = extract_refresh_token(login_b)

            # A가 먼저 rotation한다 — 이 시점에 단일 컬럼 구조였다면 사용자의 유일한
            # active_jti가 A의 새 jti로 덮어써져 B의 원래 jti가 무효로 보였을 것이다.
            refresh_a = await client_a.get("/api/v1/auth/token/refresh")
            client_a.cookies["refresh_token"] = extract_refresh_token(refresh_a)

            # B는 A의 rotation과 무관하게 자신의 원래 refresh token으로 계속 갱신할 수 있어야 한다.
            refresh_b = await client_b.get("/api/v1/auth/token/refresh")
            client_b.cookies["refresh_token"] = extract_refresh_token(refresh_b)

            # 양쪽 다 다시 한번 rotation해도 서로 간섭하지 않아야 한다.
            refresh_a_again = await client_a.get("/api/v1/auth/token/refresh")
            refresh_b_again = await client_b.get("/api/v1/auth/token/refresh")

        assert refresh_a.status_code == status.HTTP_200_OK
        assert refresh_b.status_code == status.HTTP_200_OK
        assert refresh_a_again.status_code == status.HTTP_200_OK
        assert refresh_b_again.status_code == status.HTTP_200_OK

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

    async def test_logout_after_rotation_invalidates_and_deletes_current_cookie(self):
        """#206: rotation으로 로그인 시점과 다른 access/refresh token으로 바뀐 뒤에도
        로그아웃이 정상 동작해야 하고, `resolve_logout_user()`의 fallback·쿠키 삭제 로직이
        rotation으로 교체된 최신 토큰과 충돌하지 않아야 한다."""
        email = f"logout-rotate-{uuid4().hex[:8]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "로테이션후로그아웃테스터"},
            )
            login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
            client.cookies["refresh_token"] = extract_refresh_token(login_response)

            rotate_response = await client.get("/api/v1/auth/token/refresh")
            rotated_access_token = rotate_response.json()["access_token"]
            rotated_refresh_token = extract_refresh_token(rotate_response)
            client.cookies["refresh_token"] = rotated_refresh_token

            logout_response = await client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {rotated_access_token}"},
            )

            me_response = await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {rotated_access_token}"},
            )
            client.cookies["refresh_token"] = rotated_refresh_token
            refresh_after_logout = await client.get("/api/v1/auth/token/refresh")

        assert logout_response.status_code == status.HTTP_200_OK
        assert any(
            "refresh_token=" in header and "Max-Age=0" in header
            for header in logout_response.headers.get_list("set-cookie")
        )
        assert me_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert refresh_after_logout.status_code == status.HTTP_401_UNAUTHORIZED

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

    async def test_logout_immediately_invalidates_the_now_correctly_long_lived_refresh_token(self):
        """4차 리뷰 배경: refresh token 수명 버그(55년) 수정 후에는 실제 설정된 만큼(현재
        `REFRESH_TOKEN_EXPIRE_MINUTES` 기본 7일)의 refresh token이 발급된다. 만약
        `token_version` 재검증이 없었다면 탈취된 토큰이 그 기간 내내 유효했을 것이다 —
        로그아웃이 그 값을 즉시 무효화한다는 것을 이 실제 장수명 토큰으로 직접 확인해, 수명
        수정이 보안 완화장치(`token_version` 재검증)를 우회하지 않는다는 점을 명시적으로
        검증한다."""
        email = f"logout-longlived-{uuid4().hex[:10]}@example.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/signup",
                json={"email": email, "password": "Password123!", "name": "장수명토큰테스터"},
            )
            login_response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "Password123!"},
            )
            access_token = login_response.json()["access_token"]
            refresh_token = extract_refresh_token(login_response)

            # 수정 후 실제로 설정된 만큼 장수명 토큰인지 먼저 확인 — 이 값이 작으면 아래
            # 무효화 검증이 우연히 "이미 만료돼서" 통과하는 거짓 양성이 됩니다.
            decoded_refresh = RefreshToken(token=refresh_token)
            exp = datetime.fromtimestamp(decoded_refresh.payload["exp"], tz=UTC)
            half_lifetime = timedelta(minutes=config.REFRESH_TOKEN_EXPIRE_MINUTES / 2)
            assert exp > datetime.now(UTC) + half_lifetime

            await client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            client.cookies["refresh_token"] = refresh_token
            refresh_response = await client.get("/api/v1/auth/token/refresh")

        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED
        assert refresh_response.json()["code"] == "INVALID_TOKEN"
