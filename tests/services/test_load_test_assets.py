from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "load_testing" / "validate_load_test_assets.py"


def _load_validator_module():
    spec = importlib.util.spec_from_file_location("validate_load_test_assets", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_locust_stub(monkeypatch) -> None:
    locust_stub: Any = types.ModuleType("locust")

    class HttpUser:
        pass

    def between(min_wait: float, max_wait: float) -> tuple[float, float]:
        return (min_wait, max_wait)

    def task(arg: object = None) -> Callable[..., object]:
        if callable(arg):
            return arg

        def decorator(function: Callable[..., object]) -> Callable[..., object]:
            return function

        return decorator

    locust_stub.HttpUser = HttpUser
    locust_stub.between = between
    locust_stub.task = task
    monkeypatch.setitem(sys.modules, "locust", locust_stub)


def _load_load_test_module(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "load_tests" / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_locustfile(module_name: str):
    return _load_load_test_module(module_name, "locustfile.py")


def test_load_test_framework_assets_are_aligned() -> None:
    module = _load_validator_module()

    assert module.validate_assets(ROOT) == []


def test_locustfile_defaults_to_openapi_liveness_not_missing_health_route() -> None:
    locustfile = (ROOT / "load_tests" / "locustfile.py").read_text(encoding="utf-8")

    assert 'DEFAULT_SMOKE_PATH = "/api/openapi.json"' in locustfile
    assert 'DEFAULT_SMOKE_PATH = "/api/v1/health"' not in locustfile


def test_load_test_runbook_is_linked_from_testing_and_deployment_docs() -> None:
    testing_doc = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
    deployment_doc = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    assert "testing/load-testing-627.md" in testing_doc
    assert "./testing/load-testing-627.md" in deployment_doc
    assert "Production 수용량 승인은 별도 후속 검증" in deployment_doc


def test_locustfile_imports_with_minimal_locust_stub(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)

    module = _load_load_test_module("issue_627_locustfile", "locustfile.py")

    assert module.DEFAULT_SMOKE_PATH == "/api/openapi.json"
    assert module._configured_smoke_path() == "/api/openapi.json"
    assert module._configured_expected_status() == 200
    assert module._headers() == {"Accept": "application/json"}


def test_locustfile_rejects_relative_smoke_path_with_minimal_locust_stub(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    monkeypatch.setenv("LOAD_TEST_SMOKE_PATH", "api/openapi.json")

    module = _load_load_test_module("issue_627_locustfile_invalid", "locustfile.py")

    try:
        module._configured_smoke_path()
    except RuntimeError as exc:
        assert "absolute path" in str(exc)
    else:
        raise AssertionError("relative LOAD_TEST_SMOKE_PATH should fail")


def test_auth_smoke_imports_with_minimal_locust_stub(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)

    module = _load_load_test_module("issue_627_auth_smoke", "auth_smoke.py")

    assert module.LOGIN_PATH == "/api/v1/auth/login"
    assert module.TOKEN_REFRESH_PATH == "/api/v1/auth/token/refresh"
    assert module.USER_ME_PATH == "/api/v1/users/me"
    assert module._access_token_from_response({"access_token": "token"}) == "token"
    assert module._access_token_from_response({"access_token": ""}) is None
    assert module._refresh_token_from_response(types.SimpleNamespace(cookies={"refresh_token": "refresh"})) == "refresh"
    assert module._refresh_token_from_set_cookie("refresh_token=refresh; HttpOnly") == "refresh"
    assert module._refresh_token_from_response(types.SimpleNamespace(cookies={}, headers={})) is None
    assert module._include_refresh() is False
    assert module._logout_on_stop() is False

    monkeypatch.setenv("LOAD_TEST_AUTH_INCLUDE_REFRESH", "true")
    monkeypatch.setenv("LOAD_TEST_AUTH_LOGOUT_ON_STOP", "true")

    assert module._include_refresh() is True
    assert module._logout_on_stop() is True


def test_auth_smoke_requires_explicit_test_credentials(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    monkeypatch.delenv("LOAD_TEST_AUTH_EMAIL", raising=False)

    module = _load_load_test_module("issue_627_auth_smoke_missing_env", "auth_smoke.py")

    try:
        module._required_env("LOAD_TEST_AUTH_EMAIL")
    except RuntimeError as exc:
        assert "LOAD_TEST_AUTH_EMAIL" in str(exc)
    else:
        raise AssertionError("missing auth smoke credential should fail")


def test_schedule_smoke_imports_with_minimal_locust_stub(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    monkeypatch.setenv("LOAD_TEST_SCHEDULE_DATE", "2026-09-18")
    monkeypatch.setenv("LOAD_TEST_SCHEDULE_DETAIL_LIMIT", "2")

    module = _load_load_test_module("issue_743_schedule_smoke", "schedule_smoke.py")

    assert module.LOGIN_PATH == "/api/v1/auth/login"
    assert module.MEDICATION_OCCURRENCES_PATH == "/api/v1/medication-occurrences"
    assert module._configured_schedule_date() == "2026-09-18"
    assert module._configured_detail_limit() == 2
    assert module._access_token_from_response({"access_token": "token"}) == "token"
    assert module._occurrence_id({"occurrence_id": "occurrence-1"}) == "occurrence-1"
    assert module._auth_headers("token") == {
        "Authorization": "Bearer token",
        "Accept": "application/json",
    }


def test_schedule_smoke_rejects_invalid_configuration(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    monkeypatch.setenv("LOAD_TEST_SCHEDULE_DATE", "2026/09/18")
    monkeypatch.setenv("LOAD_TEST_SCHEDULE_DETAIL_LIMIT", "21")

    module = _load_load_test_module("issue_743_schedule_smoke_invalid", "schedule_smoke.py")

    try:
        module._configured_schedule_date()
    except RuntimeError as exc:
        assert "YYYY-MM-DD" in str(exc)
    else:
        raise AssertionError("invalid schedule date should fail")

    try:
        module._configured_detail_limit()
    except RuntimeError as exc:
        assert "between 0 and 20" in str(exc)
    else:
        raise AssertionError("invalid detail limit should fail")


def test_schedule_smoke_requires_explicit_test_credentials(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    monkeypatch.delenv("LOAD_TEST_AUTH_EMAIL", raising=False)

    module = _load_load_test_module("issue_743_schedule_smoke_missing_env", "schedule_smoke.py")

    try:
        module._required_env("LOAD_TEST_AUTH_EMAIL")
    except RuntimeError as exc:
        assert "LOAD_TEST_AUTH_EMAIL" in str(exc)
    else:
        raise AssertionError("missing schedule smoke credential should fail")


def test_ocr_worker_smoke_imports_with_minimal_locust_stub(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)

    module = _load_load_test_module("issue_627_ocr_worker_smoke", "ocr_worker_smoke.py")

    assert module.DEFAULT_MANIFEST_PATH == "backend/app/release_validation/scenarios/ai-one-cycle-clova-openai-v1.json"
    assert module.DEFAULT_FIXTURE_PATH == "tests/fixtures/release_validation/ai_one_cycle_clova_openai_v1.png"
    assert module._idempotency_prefix() == "load-test-ocr"


def test_ocr_worker_smoke_validates_synthetic_fixture_hash(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    module = _load_load_test_module("issue_627_ocr_worker_hash", "ocr_worker_smoke.py")

    manifest = module._manifest()

    module._validate_fixture_hash(module._fixture_path(manifest), manifest)


def test_ocr_worker_smoke_maps_manifest_to_confirmed_field_values(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    module = _load_load_test_module("issue_627_ocr_worker_fields", "ocr_worker_smoke.py")

    values = module._expected_confirmed_values(module._manifest())

    assert values[(0, "PRESCRIBED_DATE")] == "2026-08-21"
    assert values[(1, "MEDICATION_NAME")] == "합성의약품에이정"
    assert values[(1, "FREQUENCY_PER_DAY")] == "2"
    assert values[(1, "TIMING")] == "아침 저녁 식후"


def test_ocr_worker_smoke_requires_bearer_token_without_exposing_value(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    monkeypatch.delenv("LOAD_TEST_BEARER_TOKEN", raising=False)
    module = _load_load_test_module("issue_627_ocr_worker_token", "ocr_worker_smoke.py")

    try:
        module._auth_headers()
    except RuntimeError as exc:
        assert "LOAD_TEST_BEARER_TOKEN is required" in str(exc)
        assert "Bearer" not in str(exc)
    else:
        raise AssertionError("missing LOAD_TEST_BEARER_TOKEN should fail")


class _RequestEventRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def fire(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _ocr_smoke_user_with_request_recorder(module: Any) -> tuple[Any, _RequestEventRecorder]:
    recorder = _RequestEventRecorder()
    user = module.OcrWorkerSmokeUser()
    user.environment = types.SimpleNamespace(events=types.SimpleNamespace(request=recorder))
    return user, recorder


def _assert_single_flow_failure(recorder: _RequestEventRecorder, *, reason: str) -> None:
    assert len(recorder.calls) == 1
    event = recorder.calls[0]
    assert event["request_type"] == "FLOW"
    assert event["name"] == "ocr-smoke:flow-failed"
    assert event["response_time"] == 0
    assert event["response_length"] == 0
    assert event["context"] == {"reason": reason}
    assert str(event["exception"]) == f"OCR smoke flow failed: {reason}"
    assert "Bearer" not in str(event["exception"])


def test_ocr_worker_smoke_terminal_job_status_records_flow_failure(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    module = _load_load_test_module("issue_627_ocr_worker_terminal_failure", "ocr_worker_smoke.py")
    user, recorder = _ocr_smoke_user_with_request_recorder(module)
    user._json_request = lambda *args, **kwargs: {"status": "FAILED"}

    result = user._wait_for_completed_job({"status_url": "/api/v1/jobs/synthetic"}, {})

    assert result is None
    _assert_single_flow_failure(recorder, reason="terminal-status")


def test_ocr_worker_smoke_timeout_records_flow_failure(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    module = _load_load_test_module("issue_627_ocr_worker_timeout_failure", "ocr_worker_smoke.py")
    user, recorder = _ocr_smoke_user_with_request_recorder(module)
    times = iter([0.0, 2.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(module, "_max_wait_seconds", lambda: 1.0)

    result = user._wait_for_completed_job({"status_url": "/api/v1/jobs/synthetic"}, {})

    assert result is None
    _assert_single_flow_failure(recorder, reason="timeout")


def test_ocr_worker_smoke_missing_required_fields_records_flow_failure(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    module = _load_load_test_module("issue_627_ocr_worker_missing_fields_failure", "ocr_worker_smoke.py")
    user, recorder = _ocr_smoke_user_with_request_recorder(module)

    reviewed = user._review_extracted_fields({"fields": []}, module._manifest(), {})

    assert reviewed is False
    _assert_single_flow_failure(recorder, reason="missing-required-fields")
