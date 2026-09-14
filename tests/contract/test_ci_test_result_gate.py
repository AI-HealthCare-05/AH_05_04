import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GATE = PROJECT_ROOT / "scripts" / "ci" / "verify_ci_test_results.py"

SUCCESS_ENV = {
    "CLASSIFIER_RESULT": "success",
    "INVENTORY_RESULT": "success",
    "MIGRATION_REQUIRED": "true",
    "MIGRATION_RESULT": "success",
    "BACKEND_REQUIRED": "true",
    "BACKEND_RESULT": "success",
    "RAG_REQUIRED": "true",
    "RAG_RESULT": "success",
    "CONTRACT_REQUIRED": "true",
    "CONTRACT_RESULT": "success",
    "WORKER_REQUIRED": "true",
    "WORKER_RESULT": "success",
}


def _run_gate(**overrides: str) -> subprocess.CompletedProcess[str]:
    assert GATE.is_file(), "CI result gate must exist"
    environment = os.environ.copy()
    environment.update(SUCCESS_ENV)
    environment.update(overrides)
    return subprocess.run(
        [sys.executable, str(GATE)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_gate_accepts_successful_required_jobs() -> None:
    result = _run_gate()

    assert result.returncode == 0
    assert "Python test jobs satisfied the selected CI scope." in result.stdout


def test_gate_accepts_skipped_unselected_jobs() -> None:
    result = _run_gate(
        MIGRATION_REQUIRED="false",
        MIGRATION_RESULT="skipped",
        BACKEND_REQUIRED="false",
        BACKEND_RESULT="skipped",
        RAG_REQUIRED="false",
        RAG_RESULT="skipped",
        WORKER_REQUIRED="false",
        WORKER_RESULT="skipped",
    )

    assert result.returncode == 0


@pytest.mark.parametrize("result_name", ["failure", "cancelled"])
def test_gate_rejects_failure_or_cancellation_even_for_an_unselected_job(result_name: str) -> None:
    result = _run_gate(WORKER_REQUIRED="false", WORKER_RESULT=result_name)

    assert result.returncode == 1
    assert f"worker={result_name} (required=false)" in result.stderr


def test_gate_rejects_a_skipped_required_job() -> None:
    result = _run_gate(BACKEND_REQUIRED="true", BACKEND_RESULT="skipped")

    assert result.returncode == 1
    assert "backend=skipped (required=true)" in result.stderr


def test_gate_rejects_a_failed_rag_job_required_by_backend_scope() -> None:
    result = _run_gate(RAG_REQUIRED="true", RAG_RESULT="failure")

    assert result.returncode == 1
    assert "rag=failure (required=true)" in result.stderr


@pytest.mark.parametrize("variable", ["CLASSIFIER_RESULT", "INVENTORY_RESULT"])
def test_gate_requires_always_running_preflight_jobs(variable: str) -> None:
    result = _run_gate(**{variable: "failure"})

    assert result.returncode == 1
    assert variable.removesuffix("_RESULT").lower() in result.stderr
