"""Medication schedule read smoke scenario for #743.

This scenario keeps the first schedule load-test slice read-only:

    login -> daily medication occurrences -> occurrence medication detail

It does not create, update, cancel, or check in schedules. Use only synthetic
test accounts and fixture data approved for the target environment.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from locust import HttpUser, between, task

LOGIN_PATH = "/api/v1/auth/login"
MEDICATION_OCCURRENCES_PATH = "/api/v1/medication-occurrences"
SCHEDULE_DATE_ENV = "LOAD_TEST_SCHEDULE_DATE"
DETAIL_LIMIT_ENV = "LOAD_TEST_SCHEDULE_DETAIL_LIMIT"
DEFAULT_DETAIL_LIMIT = 3
LOCAL_TIMEZONE = ZoneInfo("Asia/Seoul")


class ScheduleReadSmokeUser(HttpUser):
    """Read-only medication schedule smoke user for #743 1차-3."""

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.access_token: str | None = None
        self.access_token = _login(self.client)

    @task(3)
    def read_daily_medication_schedule(self) -> None:
        if not self.access_token:
            _report_missing_token(self.environment, "schedule-smoke:missing-access-token")
            return

        target_date = _configured_schedule_date()
        with self.client.get(
            f"{MEDICATION_OCCURRENCES_PATH}?date={target_date}",
            headers=_auth_headers(self.access_token),
            name="schedule-smoke:occurrences",
            catch_response=True,
        ) as response:
            occurrences = _occurrences_from_response(response)
            if occurrences is None:
                return

        for occurrence in occurrences[: _configured_detail_limit()]:
            occurrence_id = _occurrence_id(occurrence)
            if occurrence_id is None:
                continue
            self._read_occurrence_medication(occurrence_id)

    def _read_occurrence_medication(self, occurrence_id: str) -> None:
        assert self.access_token is not None
        with self.client.get(
            f"{MEDICATION_OCCURRENCES_PATH}/{occurrence_id}/medication",
            headers=_auth_headers(self.access_token),
            name="schedule-smoke:occurrence-medication",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}")


def _login(client: Any) -> str | None:
    payload = {
        "email": _required_env("LOAD_TEST_AUTH_EMAIL"),
        "password": _required_env("LOAD_TEST_AUTH_PASSWORD"),
    }
    with client.post(LOGIN_PATH, json=payload, name="schedule-smoke:login", catch_response=True) as response:
        if response.status_code != 200:
            response.failure(f"expected 200, got {response.status_code}")
            return None
        token = _access_token_from_response(response.json())
        if token is None:
            response.failure("response did not include access_token")
            return None
        return token


def _occurrences_from_response(response: Any) -> list[dict[str, Any]] | None:
    if response.status_code != 200:
        response.failure(f"expected 200, got {response.status_code}")
        return None
    payload = response.json()
    if not isinstance(payload, dict):
        response.failure("response body is not an object")
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        response.failure("response data is not an object")
        return None
    occurrences = data.get("occurrences")
    if not isinstance(occurrences, list):
        response.failure("response data.occurrences is not a list")
        return None
    return [occurrence for occurrence in occurrences if isinstance(occurrence, dict)]


def _occurrence_id(occurrence: dict[str, Any]) -> str | None:
    occurrence_id = occurrence.get("occurrence_id")
    if isinstance(occurrence_id, str) and occurrence_id.strip():
        return occurrence_id
    return None


def _access_token_from_response(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    token = payload.get("access_token")
    return token if isinstance(token, str) and token.strip() else None


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _configured_schedule_date() -> str:
    value = os.environ.get(SCHEDULE_DATE_ENV, "").strip()
    if value:
        _validate_local_date(value, SCHEDULE_DATE_ENV)
        return value
    return datetime.now(LOCAL_TIMEZONE).date().isoformat()


def _configured_detail_limit() -> int:
    raw_value = os.environ.get(DETAIL_LIMIT_ENV, str(DEFAULT_DETAIL_LIMIT)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{DETAIL_LIMIT_ENV} must be an integer") from exc
    if value < 0 or value > 20:
        raise RuntimeError(f"{DETAIL_LIMIT_ENV} must be between 0 and 20")
    return value


def _validate_local_date(value: str, env_name: str) -> None:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError(f"{env_name} must use YYYY-MM-DD format") from exc
    if parsed.date().isoformat() != value:
        raise RuntimeError(f"{env_name} must use YYYY-MM-DD format")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for schedule smoke load tests")
    return value


def _report_missing_token(environment: Any, name: str) -> None:
    environment.events.request.fire(
        request_type="AUTH",
        name=name,
        response_time=0,
        response_length=0,
        exception=RuntimeError("login did not produce an access token"),
        context={},
    )
