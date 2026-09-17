"""Issue #627 load-test framework smoke entrypoint.

This file intentionally contains only a configurable liveness smoke task.
API-specific scenarios will be added after the public API surface stabilizes.
Run it with Locust, for example:

    uvx locust -f load_tests/locustfile.py --host http://127.0.0.1:8000 --headless -u 1 -r 1 -t 30s
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task

DEFAULT_SMOKE_PATH = "/api/openapi.json"
DEFAULT_EXPECTED_STATUS = 200


class FrameworkSmokeUser(HttpUser):
    """Minimal configurable smoke user for #627 framework validation."""

    wait_time = between(0.5, 2.0)

    @task
    def smoke_configured_path(self) -> None:
        path = _configured_smoke_path()
        expected_status = _configured_expected_status()
        with self.client.get(path, headers=_headers(), name="framework-smoke", catch_response=True) as response:
            if response.status_code != expected_status:
                response.failure(f"expected {expected_status}, got {response.status_code}")


def _configured_smoke_path() -> str:
    path = os.environ.get("LOAD_TEST_SMOKE_PATH", DEFAULT_SMOKE_PATH).strip()
    if not path.startswith("/"):
        raise RuntimeError("LOAD_TEST_SMOKE_PATH must be an absolute path such as /api/openapi.json")
    return path


def _configured_expected_status() -> int:
    raw_status = os.environ.get("LOAD_TEST_EXPECT_STATUS", str(DEFAULT_EXPECTED_STATUS)).strip()
    try:
        status = int(raw_status)
    except ValueError as exc:
        raise RuntimeError("LOAD_TEST_EXPECT_STATUS must be an integer HTTP status") from exc
    if status < 100 or status > 599:
        raise RuntimeError("LOAD_TEST_EXPECT_STATUS must be a valid HTTP status")
    return status


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("LOAD_TEST_BEARER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
