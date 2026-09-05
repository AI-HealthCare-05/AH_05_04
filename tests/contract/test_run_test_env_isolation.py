"""`scripts/ci/run_test.sh`가 컨테이너용 설정을 host 테스트에 흘리지 않는지 확인합니다.

`run_test.sh`는 `uv run --env-file "$ENV_FILE"`로 `envs/.local.env` 전체를 주입합니다.
그 파일은 컨테이너용이라 `STORAGE_DIR`이 컨테이너 절대경로이고, local live 검증 절차
(`docs/validation/ai-one-cycle-release.md`)는 `RELEASE_VALIDATION_ALLOWED`와
`OCR_STRUCTURE_LLM_ENABLED`를 켜두도록 안내합니다. 이 값들이 host 테스트로 새어 들어가면
실제 결함이 아닌 연쇄 실패가 발생합니다(IT-1 QA 2026-09-02, 25건).

uv는 shell 환경변수를 `--env-file`보다 우선 적용하므로 `env VAR=... uv run` 방식으로
덮어쓸 수 있습니다. 아래 테스트는 그 override 목록이 유지되는지 고정합니다.
"""

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TEST_SCRIPT = PROJECT_ROOT / "scripts" / "ci" / "run_test.sh"
GITHUB_ACTIONS_CHECKS = PROJECT_ROOT / ".github" / "workflows" / "checks.yml"
AI_WORKER_ROOT = PROJECT_ROOT / "ai_worker"
EXAMPLE_LOCAL_ENV = PROJECT_ROOT / "envs" / "example.local.env"

# ENV_FILE에서 값을 물려받으면 host 테스트가 깨지는 설정과, run_test.sh가 강제해야 하는 값입니다.
CONTAINER_ONLY_SETTINGS = {
    "RELEASE_VALIDATION_ALLOWED": "false",
    "OCR_STRUCTURE_LLM_ENABLED": "false",
}

REQUIRED_WORKER_INTEGRATION_TARGETS = (
    "tests/integration/test_worker_job_execution_repository.py::test_worker_runtime_completes_real_redis_postgresql_ocr_one_cycle",
    "tests/integration/test_worker_dlq_outbox_repository.py",
    "tests/integration/test_worker_recovery_repository.py",
)


def _function_body(function_name: str) -> str:
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")
    body = re.search(rf"{function_name}\(\)\s*\{{(?P<body>.*?)\n\}}", script, re.DOTALL)

    assert body is not None, f"{function_name}() 함수를 찾지 못했습니다."

    return body.group("body")


def _run_with_backend_test_database_body() -> str:
    return _function_body("run_with_backend_test_database")


def _run_with_worker_test_environment_body() -> str:
    return _function_body("run_with_worker_test_environment")


@pytest.mark.parametrize(("name", "expected"), sorted(CONTAINER_ONLY_SETTINGS.items()))
def test_run_test_script_forces_test_value_for_container_only_setting(name: str, expected: str) -> None:
    assert f"{name}={expected}" in _run_with_backend_test_database_body(), (
        f"{name}을(를) test 기준값으로 덮어쓰지 않으면 envs/.local.env의 현재 값이 host 테스트에 적용됩니다."
    )
    assert f"{name}={expected}" in _run_with_worker_test_environment_body(), (
        f"{name}을(를) test 기준값으로 덮어쓰지 않으면 envs/.local.env의 현재 값이 worker 테스트에 적용됩니다."
    )


def test_run_test_script_replaces_container_storage_dir_with_host_directory() -> None:
    """`STORAGE_DIR`은 고정 문자열이 아니라 host 임시 디렉터리로 덮어써야 합니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")

    assert 'STORAGE_DIR="$TEST_STORAGE_DIR"' in _run_with_backend_test_database_body()
    assert 'STORAGE_DIR="$TEST_STORAGE_DIR"' in _run_with_worker_test_environment_body()
    assert 'TEST_STORAGE_DIR="$(mktemp -d)"' in script
    assert "trap 'rm -rf \"$TEST_STORAGE_DIR\"' EXIT" in script


def test_run_test_script_exposes_backend_and_shared_contract_packages_for_backend_tests() -> None:
    """Console entry points must import both `app` and root-level shared contracts."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")

    assert 'REPOSITORY_ROOT="$(pwd)"' in script
    assert 'PYTHONPATH="$REPOSITORY_ROOT/backend:$REPOSITORY_ROOT"' in _run_with_backend_test_database_body()


def test_run_test_script_excludes_backend_from_ai_worker_unit_test_pythonpath() -> None:
    """AI Worker 단위 테스트는 backend/app이 import path에 없어야 물리 경계 위반을 잡습니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")
    worker_body = _run_with_worker_test_environment_body()

    assert "coverage run --append -m pytest \\" in script
    assert "ai_worker/tests/core" in script
    assert "ai_worker/tests/ocr" in script
    assert "ai_worker/tests/rag" in script
    assert "./ai_worker/tests/rag" in script
    assert 'PYTHONPATH="$REPOSITORY_ROOT"' in worker_body
    assert 'PYTHONPATH="$REPOSITORY_ROOT/backend:$REPOSITORY_ROOT"' not in worker_body


def _is_forbidden_worker_backend_import(module_name: str) -> bool:
    return (
        module_name == "app"
        or module_name.startswith("app.")
        or module_name == "backend.app"
        or module_name.startswith("backend.app.")
    )


def test_ai_worker_source_does_not_import_backend_app_modules() -> None:
    """Worker 소스는 backend/app 내부 모듈을 직접 import하지 않습니다."""
    offenders: list[str] = []

    for source_path in sorted(AI_WORKER_ROOT.rglob("*.py")):
        relative_path = source_path.relative_to(PROJECT_ROOT)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(relative_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden_worker_backend_import(alias.name):
                        offenders.append(f"{relative_path}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module_name = node.module or ""
                imports_backend_app = module_name == "backend" and any(alias.name == "app" for alias in node.names)
                if _is_forbidden_worker_backend_import(module_name) or imports_backend_app:
                    offenders.append(f"{relative_path}:{node.lineno} from {module_name} import ...")

    assert not offenders, "AI Worker must not import backend/app modules:\n" + "\n".join(offenders)


def test_github_actions_excludes_backend_from_ai_worker_unit_test_pythonpath() -> None:
    """GitHub Actions에서도 Worker 단위 테스트는 backend/app 경로 없이 별도 실행해야 합니다."""
    workflow = GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8")

    assert "PYTHONPATH: ${{ github.workspace }}/backend:${{ github.workspace }}" in workflow
    assert (
        "PYTHONPATH: ${{ github.workspace }}\n        run: |\n          uv run coverage run --append -m pytest ai_worker/tests/core ai_worker/tests/ocr ai_worker/tests/rag"
        in workflow
    )
    assert (
        "uv run coverage run -m pytest backend/app tests/contract ai_worker/tests/core ai_worker/tests/ocr"
        not in workflow
    )


@pytest.mark.parametrize("target", REQUIRED_WORKER_INTEGRATION_TARGETS)
def test_run_test_script_includes_worker_recovery_integration_target(target: str) -> None:
    """GitHub Actions와 로컬 기본 runner가 같은 Worker 복구 경계를 검증해야 합니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")
    workflow = GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8")

    assert target in script
    assert target in workflow


def test_example_local_env_storage_dir_is_a_container_path() -> None:
    """위 override가 필요한 이유를 고정합니다 — 이 값이 host 경로로 바뀌면 override 근거도 바뀝니다."""
    storage_dir = re.search(
        r"^STORAGE_DIR=(?P<value>.+)$",
        EXAMPLE_LOCAL_ENV.read_text(encoding="utf-8"),
        re.MULTILINE,
    )

    assert storage_dir is not None
    assert storage_dir.group("value").strip().startswith("/app/")
