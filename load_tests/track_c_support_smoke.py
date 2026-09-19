"""Track C support read smoke scenario for #743.

This scenario keeps the Track C support slice read-only:

    login -> support offers -> optional plan/resources/followup reads

It does not create safety assessments, barrier responses, action plans, or
followups. Use only synthetic test accounts and fixture identifiers approved for
load-test evidence.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from locust import HttpUser, between, task

LOGIN_PATH = "/api/v1/auth/login"
TRACK_C_BARRIER_RESPONSE_ID_ENV = "LOAD_TEST_TRACK_C_BARRIER_RESPONSE_ID"
TRACK_C_SUPPORT_PLAN_ID_ENV = "LOAD_TEST_TRACK_C_SUPPORT_PLAN_ID"
TRACK_C_TRAVEL_SITUATION_ENV = "LOAD_TEST_TRACK_C_TRAVEL_SITUATION"
TRACK_C_SUBREASON_CODE_ENV = "LOAD_TEST_TRACK_C_SUBREASON_CODE"
VALID_TRAVEL_SITUATIONS = frozenset({"SCHEDULE_CHANGED", "MEDICATION_NOT_WITH_ME"})


class TrackCSupportSmokeUser(HttpUser):
    """Read-only Track C support smoke user for #743 stage 1-4."""

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.access_token: str | None = None
        self.access_token = _login(self.client)

    @task(3)
    def read_track_c_supports(self) -> None:
        if not self.access_token:
            _report_flow_failure(self.environment, "missing-access-token")
            return

        barrier_response_id = _required_env(TRACK_C_BARRIER_RESPONSE_ID_ENV)
        with self.client.get(
            _support_offers_path(barrier_response_id),
            headers=_auth_headers(self.access_token),
            name="track-c-support-smoke:support-offers",
            catch_response=True,
        ) as response:
            offer = _offer_from_response(response)
            if offer is None:
                return

        self._read_optional_plan_resources()

    def _read_optional_plan_resources(self) -> None:
        assert self.access_token is not None
        plan_id = _optional_env(TRACK_C_SUPPORT_PLAN_ID_ENV)
        if plan_id is None:
            return

        endpoints = (
            (
                f"/api/v1/support-action-plans/{plan_id}",
                "track-c-support-smoke:action-plan",
                _plan_from_response,
            ),
            (
                f"/api/v1/support-action-plans/{plan_id}/resources",
                "track-c-support-smoke:plan-resources",
                _resources_from_response,
            ),
            (
                f"/api/v1/support-action-plans/{plan_id}/followups",
                "track-c-support-smoke:plan-followup",
                _followup_from_response,
            ),
        )
        for path, name, parser in endpoints:
            with self.client.get(
                path,
                headers=_auth_headers(self.access_token),
                name=name,
                catch_response=True,
            ) as response:
                parser(response)


def _login(client: Any) -> str | None:
    payload = {
        "email": _required_env("LOAD_TEST_AUTH_EMAIL"),
        "password": _required_env("LOAD_TEST_AUTH_PASSWORD"),
    }
    with client.post(LOGIN_PATH, json=payload, name="track-c-support-smoke:login", catch_response=True) as response:
        if response.status_code != 200:
            response.failure(f"expected 200, got {response.status_code}")
            return None
        token = _access_token_from_response(response.json())
        if token is None:
            response.failure("response did not include access_token")
            return None
        return token


def _support_offers_path(barrier_response_id: str) -> str:
    query = _support_offer_query()
    path = f"/api/v1/barrier-responses/{barrier_response_id}/supports"
    return f"{path}?{query}" if query else path


def _support_offer_query() -> str:
    params: list[tuple[str, str]] = []
    travel_situation = _optional_env(TRACK_C_TRAVEL_SITUATION_ENV)
    if travel_situation is not None:
        if travel_situation not in VALID_TRAVEL_SITUATIONS:
            raise RuntimeError(f"{TRACK_C_TRAVEL_SITUATION_ENV} must be one of {sorted(VALID_TRAVEL_SITUATIONS)}")
        params.append(("travel_situation", travel_situation))
    subreason_code = _optional_env(TRACK_C_SUBREASON_CODE_ENV)
    if subreason_code is not None:
        params.append(("subreason_code", subreason_code))
    return urlencode(params)


def _offer_from_response(response: Any) -> dict[str, Any] | None:
    data = _data_object_from_response(response)
    if data is None:
        return None
    supports = data.get("supports")
    if not isinstance(supports, list):
        response.failure("response data.supports is not a list")
        return None
    return data


def _plan_from_response(response: Any) -> dict[str, Any] | None:
    data = _data_object_from_response(response)
    if data is None:
        return None
    plan_id = data.get("support_action_plan_id")
    if not isinstance(plan_id, str) or not plan_id.strip():
        response.failure("response data.support_action_plan_id is missing")
        return None
    return data


def _resources_from_response(response: Any) -> dict[str, Any] | None:
    data = _data_object_from_response(response)
    if data is None:
        return None
    occurrence_id = data.get("occurrence_id")
    if not isinstance(occurrence_id, str) or not occurrence_id.strip():
        response.failure("response data.occurrence_id is missing")
        return None
    return data


def _followup_from_response(response: Any) -> dict[str, Any] | None:
    if response.status_code != 200:
        response.failure(f"expected 200, got {response.status_code}")
        return None
    payload = response.json()
    if not isinstance(payload, dict):
        response.failure("response body is not an object")
        return None
    data = payload.get("data")
    if data is not None and not isinstance(data, dict):
        response.failure("response data is not null or an object")
        return None
    return data


def _data_object_from_response(response: Any) -> dict[str, Any] | None:
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
    return data


def _access_token_from_response(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    token = payload.get("access_token")
    return token if isinstance(token, str) and token.strip() else None


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _required_env(name: str) -> str:
    value = _optional_env(name)
    if value is None:
        raise RuntimeError(f"{name} is required for Track C support smoke load tests")
    return value


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value if value else None


def _report_flow_failure(environment: Any, reason: str) -> None:
    environment.events.request.fire(
        request_type="FLOW",
        name="track-c-support-smoke:flow-failed",
        response_time=0,
        response_length=0,
        exception=RuntimeError(f"Track C support smoke flow failed: {reason}"),
        context={"reason": reason},
    )
