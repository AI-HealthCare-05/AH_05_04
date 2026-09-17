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

    def task(function: Callable[..., object]) -> Callable[..., object]:
        return function

    locust_stub.HttpUser = HttpUser
    locust_stub.between = between
    locust_stub.task = task
    monkeypatch.setitem(sys.modules, "locust", locust_stub)


def _load_locustfile(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "load_tests" / "locustfile.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    module = _load_locustfile("issue_627_locustfile")

    assert module.DEFAULT_SMOKE_PATH == "/api/openapi.json"
    assert module._configured_smoke_path() == "/api/openapi.json"
    assert module._configured_expected_status() == 200
    assert module._headers() == {"Accept": "application/json"}


def test_locustfile_rejects_relative_smoke_path_with_minimal_locust_stub(monkeypatch) -> None:
    _install_locust_stub(monkeypatch)
    monkeypatch.setenv("LOAD_TEST_SMOKE_PATH", "api/openapi.json")

    module = _load_locustfile("issue_627_locustfile_invalid")

    try:
        module._configured_smoke_path()
    except RuntimeError as exc:
        assert "absolute path" in str(exc)
    else:
        raise AssertionError("relative LOAD_TEST_SMOKE_PATH should fail")
