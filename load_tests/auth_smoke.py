"""Auth baseline smoke scenario for #627.

This scenario verifies the smallest authenticated flow that can be reused for
later load-test scenarios:

    login -> /users/me

Token refresh and logout are optional because both can invalidate a shared
test account's active tokens when multiple Locust users reuse one account.
"""

from __future__ import annotations

import os
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
        with self.client.get(TOKEN_REFRESH_PATH, name="auth-smoke:token-refresh", catch_response=True) as response:
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

    def on_stop(self) -> None:
        if _logout_on_stop() and self.access_token:
            self.client.post(
                LOGOUT_PATH,
                headers=self._auth_headers(),
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

    def _has_access_token(self) -> bool:
        if self.access_token:
            return True
        self.environment.events.request.fire(
            request_type="AUTH",
            name="auth-smoke:missing-access-token",
            response_time=0,
            response_length=0,
            exception=RuntimeError("login or refresh did not produce an access token"),
            context={},
        )
        return False

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}


def _access_token_from_response(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    token = payload.get("access_token")
    return token if isinstance(token, str) and token.strip() else None


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for auth smoke load tests")
    return value


def _include_refresh() -> bool:
    return os.environ.get("LOAD_TEST_AUTH_INCLUDE_REFRESH", "false").strip().lower() in {"1", "true", "yes"}


def _logout_on_stop() -> bool:
    return os.environ.get("LOAD_TEST_AUTH_LOGOUT_ON_STOP", "false").strip().lower() in {"1", "true", "yes"}
