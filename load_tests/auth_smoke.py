"""Auth baseline smoke scenario for #627.

This scenario verifies the smallest authenticated flow that can be reused for
later load-test scenarios:

    login -> /users/me

Token refresh and logout are optional because both can invalidate a shared
test account's active tokens when multiple Locust users reuse one account.
Refresh cookies are kept in memory only and are never logged or written to CSV.
"""

from __future__ import annotations

import os
from http.cookies import SimpleCookie
from typing import Any

from locust import HttpUser, between, task

LOGIN_PATH = "/api/v1/auth/login"
TOKEN_REFRESH_PATH = "/api/v1/auth/token/refresh"
USER_ME_PATH = "/api/v1/users/me"
LOGOUT_PATH = "/api/v1/auth/logout"


class AuthSmokeUser(HttpUser):
    """Minimal authenticated smoke user for #627 1차-1 Auth baseline."""

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._login()

    @task(3)
    def get_current_user(self) -> None:
        if not self._has_access_token():
            return
        with self.client.get(
            USER_ME_PATH,
            headers=self._auth_headers(),
            name="auth-smoke:users-me",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")

    @task(1)
    def refresh_access_token(self) -> None:
        if not _include_refresh():
            return
        if not self.refresh_token:
            self._report_missing_token("auth-smoke:missing-refresh-token", "login did not produce a refresh token")
            return
        with self.client.get(
            TOKEN_REFRESH_PATH,
            headers=self._refresh_headers(),
            name="auth-smoke:token-refresh",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")
                self.access_token = None
                return
            token = _access_token_from_response(response.json())
            if token is None:
                response.failure("response did not include access_token")
                self.access_token = None
                return
            self.access_token = token
            self.refresh_token = _refresh_token_from_response(response) or self.refresh_token

    def on_stop(self) -> None:
        if _logout_on_stop() and self.access_token:
            self.client.post(
                LOGOUT_PATH,
                headers=self._auth_headers(include_refresh_cookie=True),
                name="auth-smoke:logout",
                catch_response=False,
            )

    def _login(self) -> None:
        email = _required_env("LOAD_TEST_AUTH_EMAIL")
        password = _required_env("LOAD_TEST_AUTH_PASSWORD")
        payload = {"email": email, "password": password}
        with self.client.post(LOGIN_PATH, json=payload, name="auth-smoke:login", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")
                self.access_token = None
                return
            token = _access_token_from_response(response.json())
            if token is None:
                response.failure("response did not include access_token")
                self.access_token = None
                return
            self.access_token = token
            self.refresh_token = _refresh_token_from_response(response)
            if _include_refresh() and not self.refresh_token:
                response.failure("response did not include refresh_token cookie")

    def _has_access_token(self) -> bool:
        if self.access_token:
            return True
        self._report_missing_token(
            "auth-smoke:missing-access-token",
            "login or refresh did not produce an access token",
        )
        return False

    def _report_missing_token(self, name: str, message: str) -> None:
        self.environment.events.request.fire(
            request_type="AUTH",
            name=name,
            response_time=0,
            response_length=0,
            exception=RuntimeError(message),
            context={},
        )

    def _auth_headers(self, *, include_refresh_cookie: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        if include_refresh_cookie and self.refresh_token:
            headers["Cookie"] = f"refresh_token={self.refresh_token}"
        return headers

    def _refresh_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Cookie": f"refresh_token={self.refresh_token}"}


def _access_token_from_response(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    token = payload.get("access_token")
    return token if isinstance(token, str) and token.strip() else None


def _refresh_token_from_response(response: Any) -> str | None:
    token = response.cookies.get("refresh_token")
    if isinstance(token, str) and token.strip():
        return token
    return _refresh_token_from_set_cookie(response.headers.get("set-cookie", ""))


def _refresh_token_from_set_cookie(value: str) -> str | None:
    cookie = SimpleCookie()
    cookie.load(value)
    morsel = cookie.get("refresh_token")
    if morsel is None or not morsel.value.strip():
        return None
    return morsel.value


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for auth smoke load tests")
    return value


def _include_refresh() -> bool:
    return os.environ.get("LOAD_TEST_AUTH_INCLUDE_REFRESH", "false").strip().lower() in {"1", "true", "yes"}


def _logout_on_stop() -> bool:
    return os.environ.get("LOAD_TEST_AUTH_LOGOUT_ON_STOP", "false").strip().lower() in {"1", "true", "yes"}
