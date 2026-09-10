"""공용 test environment helper가 컨테이너용 설정을 host 테스트에 흘리지 않는지 확인합니다.

`run_test.sh`는 `uv run --env-file "$ENV_FILE"`로 `envs/.local.env` 전체를 주입합니다.
그 파일은 컨테이너용이라 `STORAGE_DIR`이 컨테이너 절대경로이고, local live 검증 절차
(`docs/validation/ai-one-cycle-release.md`)는 `RELEASE_VALIDATION_ALLOWED`와
`OCR_STRUCTURE_LLM_ENABLED`를 켜두도록 안내합니다. 이 값들이 host 테스트로 새어 들어가면
실제 결함이 아닌 연쇄 실패가 발생합니다(IT-1 QA 2026-09-02, 25건).

uv는 shell 환경변수를 `--env-file`보다 우선 적용하므로 `env VAR=... uv run` 방식으로
덮어쓸 수 있습니다. 아래 테스트는 그 override 목록이 유지되는지 고정합니다.
"""

import ast
import os
import re
import shlex
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TEST_SCRIPT = PROJECT_ROOT / "scripts" / "ci" / "run_test.sh"
PARALLEL_TEST_LANES_SCRIPT = PROJECT_ROOT / "scripts" / "ci" / "parallel_test_lanes.sh"
TEST_ENVIRONMENT_SCRIPT = PROJECT_ROOT / "scripts" / "ci" / "test_environment.sh"
GITHUB_ACTIONS_CHECKS = PROJECT_ROOT / ".github" / "workflows" / "checks.yml"
AI_WORKER_ROOT = PROJECT_ROOT / "ai_worker"
EXAMPLE_LOCAL_ENV = PROJECT_ROOT / "envs" / "example.local.env"

# ENV_FILE에서 값을 물려받으면 host 테스트가 깨지는 설정과, run_test.sh가 강제해야 하는 값입니다.
CONTAINER_ONLY_SETTINGS = {
    "RELEASE_VALIDATION_ALLOWED": "false",
    "OCR_STRUCTURE_LLM_ENABLED": "false",
}

REQUIRED_WORKER_INTEGRATION_TARGETS = (
    "tests/integration/test_worker_job_execution_repository.py",
    "tests/integration/test_worker_dlq_outbox_repository.py",
    "tests/integration/test_worker_recovery_repository.py",
)


def _function_body(function_name: str, script_path: Path = TEST_ENVIRONMENT_SCRIPT) -> str:
    script = script_path.read_text(encoding="utf-8")
    body = re.search(rf"{function_name}\(\)\s*\{{(?P<body>.*?)\n\}}", script, re.DOTALL)

    assert body is not None, f"{function_name}() 함수를 찾지 못했습니다."

    return body.group("body")


def _run_with_backend_test_database_body() -> str:
    return _function_body("run_with_backend_test_database")


def _run_with_worker_test_environment_body() -> str:
    return _function_body("run_with_worker_test_environment")


def _run_with_integration_test_environment_body() -> str:
    return _function_body("run_with_integration_test_environment")


@pytest.mark.parametrize(("name", "expected"), sorted(CONTAINER_ONLY_SETTINGS.items()))
def test_run_test_script_forces_test_value_for_container_only_setting(name: str, expected: str) -> None:
    assert f"{name}={expected}" in _run_with_backend_test_database_body(), (
        f"{name}을(를) test 기준값으로 덮어쓰지 않으면 envs/.local.env의 현재 값이 host 테스트에 적용됩니다."
    )
    assert f"{name}={expected}" in _run_with_worker_test_environment_body(), (
        f"{name}을(를) test 기준값으로 덮어쓰지 않으면 envs/.local.env의 현재 값이 worker 테스트에 적용됩니다."
    )


@pytest.mark.parametrize(
    "wrapper_body",
    [
        pytest.param(_run_with_backend_test_database_body(), id="backend"),
        pytest.param(_run_with_worker_test_environment_body(), id="worker"),
        pytest.param(_run_with_integration_test_environment_body(), id="integration"),
    ],
)
def test_run_test_wrappers_pin_empty_pytest_addopts_after_loading_env_file(wrapper_body: str) -> None:
    assert "PYTEST_ADDOPTS=" in wrapper_body
    assert 'uv run --env-file "$ENV_FILE"' in wrapper_body


def test_empty_shell_pytest_addopts_overrides_custom_uv_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text("PYTEST_ADDOPTS=-konly_old\n", encoding="utf-8")

    result = subprocess.run(
        [
            "env",
            "PYTEST_ADDOPTS=",
            "uv",
            "run",
            "--env-file",
            str(env_file),
            "python",
            "-c",
            "import os; print(repr(os.environ['PYTEST_ADDOPTS']))",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "''"


def test_run_test_script_replaces_container_storage_dir_with_host_directory() -> None:
    """`STORAGE_DIR`은 고정 문자열이 아니라 host 임시 디렉터리로 덮어써야 합니다."""
    script = TEST_ENVIRONMENT_SCRIPT.read_text(encoding="utf-8")

    assert 'STORAGE_DIR="$TEST_STORAGE_DIR"' in _run_with_backend_test_database_body()
    assert 'STORAGE_DIR="$TEST_STORAGE_DIR"' in _run_with_worker_test_environment_body()
    assert "exec mktemp -d" in script
    assert "TEST_STORAGE_DIR_OWNED=true" in script
    assert "install_test_environment_cleanup_traps" in script
    assert "trap cleanup_test_environment EXIT" in script
    assert 'rm -rf -- "$TEST_STORAGE_DIR"' in script
    assert 'rm -rf -- "$TEST_RUNNER_STATE_DIR"' in script


def test_test_environment_cleanup_preserves_inherited_directories(tmp_path: Path) -> None:
    inherited_storage = tmp_path / "inherited-storage"
    inherited_runner_state = tmp_path / "inherited-runner-state"
    inherited_storage.mkdir()
    inherited_runner_state.mkdir()

    environment = os.environ.copy()
    environment.update(
        {
            "TEST_STORAGE_DIR": str(inherited_storage),
            "TEST_RUNNER_STATE_DIR": str(inherited_runner_state),
        }
    )
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; cleanup_test_environment', "cleanup-test", str(TEST_ENVIRONMENT_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert inherited_storage.is_dir()
    assert inherited_runner_state.is_dir()


@pytest.mark.parametrize("termination", ["success", "failure", "signal"])
def test_test_environment_cleanup_removes_owned_runner_state_directory(tmp_path: Path, termination: str) -> None:
    recorded_path = tmp_path / "runner-state-path"
    if termination == "success":
        termination_command = "exit 0"
        expected_status = 0
    elif termination == "failure":
        termination_command = "exit 7"
        expected_status = 7
    else:
        termination_command = "kill -TERM $$"
        expected_status = 128 + signal.SIGTERM

    script = textwrap.dedent(
        f"""
        source "$1"
        install_test_environment_cleanup_traps
        prepare_test_runner_state_directory
        printf '%s' "$TEST_RUNNER_STATE_DIR" > "$RUNNER_STATE_PATH_RECORD"
        {termination_command}
        """
    )
    environment = os.environ.copy()
    environment["RUNNER_STATE_PATH_RECORD"] = str(recorded_path)
    result = subprocess.run(
        ["bash", "-c", script, "cleanup-test", str(TEST_ENVIRONMENT_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_status, result.stdout + result.stderr
    runner_state_path = Path(recorded_path.read_text(encoding="utf-8"))
    assert not runner_state_path.exists()


@pytest.mark.parametrize(
    ("prepare_function", "directory_name"),
    [
        ("prepare_test_storage_directory", "storage"),
        ("prepare_test_runner_state_directory", "runner-state"),
    ],
)
@pytest.mark.parametrize(
    ("signal_name", "expected_status"),
    [("HUP", 129), ("INT", 130), ("TERM", 143)],
)
def test_owned_test_directory_is_cleaned_when_signal_arrives_during_creation(
    tmp_path: Path,
    prepare_function: str,
    directory_name: str,
    signal_name: str,
    expected_status: int,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mktemp = fake_bin / "mktemp"
    creation_started = tmp_path / "creation-started"
    created_path_record = tmp_path / "created-path"
    created_directory = tmp_path / directory_name
    fake_mktemp.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            /bin/mkdir -m 700 -- "$FAKE_MKTEMP_DIRECTORY"
            printf '%s' "$FAKE_MKTEMP_DIRECTORY" > "$CREATED_PATH_RECORD"
            touch "$CREATION_STARTED"
            sleep 0.2
            printf '%s\\n' "$FAKE_MKTEMP_DIRECTORY"
            """
        ),
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)

    script = textwrap.dedent(
        f"""
        source "$1"
        {prepare_function}
        exit 99
        """
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "CREATION_STARTED": str(creation_started),
            "CREATED_PATH_RECORD": str(created_path_record),
            "FAKE_MKTEMP_DIRECTORY": str(created_directory),
        }
    )
    process = subprocess.Popen(
        ["bash", "-c", script, "cleanup-race-test", str(TEST_ENVIRONMENT_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not creation_started.is_file():
            time.sleep(0.01)
        assert creation_started.is_file(), "fake mkdir did not create the directory"

        os.killpg(process.pid, getattr(signal, f"SIG{signal_name}"))
        stdout, stderr = process.communicate(timeout=3)

        assert process.returncode == expected_status, stdout + stderr
        assert Path(created_path_record.read_text(encoding="utf-8")) == created_directory
        assert directory_name in created_directory.name
        assert not created_directory.exists()
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait(timeout=3)


def test_run_test_script_exposes_backend_and_shared_contract_packages_for_backend_tests() -> None:
    """Console entry points must import both `app` and root-level shared contracts."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")

    assert 'REPOSITORY_ROOT="$(pwd)"' in script
    assert "source scripts/ci/test_environment.sh" in script
    assert 'PYTHONPATH="$REPOSITORY_ROOT/backend:$REPOSITORY_ROOT"' in _run_with_backend_test_database_body()


def test_run_test_script_excludes_backend_from_ai_worker_unit_test_pythonpath() -> None:
    """AI Worker 단위 테스트는 backend/app이 import path에 없어야 물리 경계 위반을 잡습니다."""
    worker_lane_body = _function_body("run_worker_test_lane", RUN_TEST_SCRIPT)
    worker_body = _run_with_worker_test_environment_body()

    command_start = worker_lane_body.index("if ! run_with_worker_test_environment")
    command_end = worker_lane_body.index("; then", command_start)
    worker_command = shlex.split(worker_lane_body[command_start:command_end].replace("\\\n", " "))

    assert worker_command == [
        "if",
        "!",
        "run_with_worker_test_environment",
        "pytest",
        "-n",
        "2",
        "--dist=loadfile",
        "--max-worker-restart=0",
        "--cov",
        "--cov-report=",
        "-o",
        "cache_dir=$cache_dir",
        "ai_worker/tests/core",
        "ai_worker/tests/ocr",
        "ai_worker/tests/rag",
        "ai_worker/tests/evaluation",
    ]
    assert "ai_worker/tests/core" in worker_lane_body
    assert "ai_worker/tests/ocr" in worker_lane_body
    assert "ai_worker/tests/rag" in worker_lane_body
    assert "ai_worker/tests/evaluation" in worker_lane_body
    assert "run_with_worker_test_environment" in worker_lane_body
    assert "run_with_backend_test_database" not in worker_lane_body
    assert 'PYTHONPATH="$REPOSITORY_ROOT"' in worker_body
    assert 'PYTHONPATH="$REPOSITORY_ROOT/backend:$REPOSITORY_ROOT"' not in worker_body


def test_default_local_backend_lane_does_not_enable_xdist() -> None:
    """공유 PostgreSQL·Redis를 사용하는 로컬 Backend lane은 직렬 실행해야 합니다."""
    backend_lane_tokens = shlex.split(_function_body("run_backend_test_lane", RUN_TEST_SCRIPT).replace("\\\n", " "))

    assert not any(token.startswith(("-n", "--numprocesses")) for token in backend_lane_tokens)
    assert not any(token.startswith("--dist") for token in backend_lane_tokens)


def test_shared_environment_forces_literal_test_database_and_loopback_services() -> None:
    """두 runner 모두 개발 DB나 컨테이너 hostname으로 접속할 수 없어야 합니다."""
    script = TEST_ENVIRONMENT_SCRIPT.read_text(encoding="utf-8")
    backend_body = _run_with_backend_test_database_body()
    worker_body = _run_with_worker_test_environment_body()
    integration_body = _run_with_integration_test_environment_body()

    assert 'TEST_DATABASE_NAME="test"' in script
    assert "DROP DATABASE IF EXISTS test WITH (FORCE);" in script
    assert 'DB_NAME="$TEST_DATABASE_NAME"' in backend_body
    assert "DB_NAME=worker_unit_tests_must_not_use_database" in worker_body
    assert "DB_PORT=1" in worker_body
    assert 'DB_NAME="$TEST_DATABASE_NAME"' not in worker_body
    assert "DB_HOST=127.0.0.1" in backend_body
    assert "TEST_REDIS_HOST=127.0.0.1" in integration_body
    assert 'TEST_REDIS_PORT="$HOST_REDIS_PORT"' in integration_body
    assert "-u REDIS_PASSWORD" in integration_body
    assert "-u TEST_REDIS_PASSWORD" in integration_body


def test_default_worker_runner_preserves_approved_redis_defaults() -> None:
    """기본 Worker 단위 테스트에는 host 통합테스트용 Redis override를 주입하지 않습니다."""
    worker_body = _run_with_worker_test_environment_body()

    assert "REDIS_HOST=127.0.0.1" not in worker_body
    assert "TEST_REDIS_HOST=127.0.0.1" not in worker_body


def test_default_runner_uses_isolated_environment_for_redis_integration_tests() -> None:
    """같은 Redis 통합테스트는 어느 로컬 runner에서나 동일한 host 환경을 사용합니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")
    start = script.index("if ! run_with_integration_test_environment")
    end = script.index("; then", start)
    isolated_call = script[start:end]

    assert "tests/integration/test_outbox_publisher.py" in isolated_call
    assert "tests/integration/test_worker_job_execution_repository.py" in isolated_call
    assert "tests/integration/test_worker_dlq_outbox_repository.py" in isolated_call
    assert "tests/integration/test_worker_recovery_repository.py" in isolated_call


def test_default_runner_runs_backend_and_worker_lanes_in_parallel_with_isolated_runtime_files() -> None:
    """Shared coverage or pytest cache files can corrupt an otherwise valid parallel run."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")

    assert "source scripts/ci/parallel_test_lanes.sh" in script
    assert "run_parallel_test_lanes_with_failure_summary run_backend_test_lane run_worker_test_lane" in script
    assert "prepare_test_runner_state_directory" in script
    assert 'TEST_COVERAGE_DIR="$TEST_RUNNER_STATE_DIR/coverage"' in script
    assert 'COVERAGE_FILE="$TEST_COVERAGE_DIR/.coverage.backend"' in script
    assert 'COVERAGE_FILE="$TEST_COVERAGE_DIR/.coverage.worker"' in script
    assert 'cache_dir="$TEST_RUNNER_STATE_DIR/pytest-cache/backend"' in script
    assert 'cache_dir="$TEST_RUNNER_STATE_DIR/pytest-cache/worker"' in script
    assert 'PARALLEL_TEST_LOG_DIR="$TEST_RUNNER_STATE_DIR/test-lane-logs"' in script
    assert '"$TEST_STORAGE_DIR/coverage"' not in script
    assert 'COVERAGE_FILE="$TEST_COVERAGE_DIR/.coverage"' in script
    assert 'coverage combine "$TEST_COVERAGE_DIR"' in script
    assert PARALLEL_TEST_LANES_SCRIPT.is_file()


def test_default_runner_preserves_serial_database_setup_and_backend_integration_order() -> None:
    """DB migration과 같은 DB를 쓰는 Redis integration은 병렬 경계 밖으로 이동하면 안 됩니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")

    migration_index = script.index("run_with_backend_test_database alembic")
    migration_validation_index = script.index("run_with_backend_test_database pytest tests/migration")
    parallel_index = script.index(
        "run_parallel_test_lanes_with_failure_summary run_backend_test_lane run_worker_test_lane"
    )
    backend_pytest_index = script.index("coverage run -m pytest", script.index("run_backend_test_lane()"))
    redis_integration_index = script.index("coverage run --append -m pytest", script.index("run_backend_test_lane()"))

    assert migration_index < migration_validation_index < parallel_index
    assert backend_pytest_index < redis_integration_index < script.index("run_worker_test_lane()")


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
    workflow = yaml.safe_load(GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    backend_step = next(
        step for step in jobs["test-backend"]["steps"] if step["name"] == "Run Backend Tests with Coverage"
    )
    worker_step = next(
        step for step in jobs["test-worker"]["steps"] if step["name"] == "Run AI Worker Unit Tests with Coverage"
    )

    assert backend_step["env"]["PYTHONPATH"] == "${{ github.workspace }}/backend:${{ github.workspace }}"
    assert worker_step["env"]["PYTHONPATH"] == "${{ github.workspace }}"
    assert "backend/app" in backend_step["run"]
    assert "tests/contract" in backend_step["run"]
    assert "ai_worker/tests/core" in worker_step["run"]
    assert "ai_worker/tests/ocr" in worker_step["run"]
    assert "ai_worker/tests/rag" in worker_step["run"]
    assert "ai_worker/tests/evaluation" in worker_step["run"]
    assert "backend/app" not in worker_step["run"]


def test_github_actions_uses_fixed_xdist_only_for_the_worker_lane() -> None:
    workflow = yaml.safe_load(GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    backend_step = next(
        step for step in jobs["test-backend"]["steps"] if step["name"] == "Run Backend Tests with Coverage"
    )
    worker_step = next(
        step for step in jobs["test-worker"]["steps"] if step["name"] == "Run AI Worker Unit Tests with Coverage"
    )
    backend_command = shlex.split(backend_step["run"].replace("\\\n", " "))
    worker_command = shlex.split(worker_step["run"].replace("\\\n", " "))

    assert backend_command[:6] == ["uv", "run", "coverage", "run", "-m", "pytest"]
    assert not any(token.startswith(("-n", "--numprocesses")) for token in backend_command)
    assert worker_command[:3] == ["uv", "run", "pytest"]
    assert worker_command[3:9] == [
        "-n",
        "2",
        "--dist=loadfile",
        "--max-worker-restart=0",
        "--cov",
        "--cov-report=",
    ]


def test_github_actions_runs_python_test_lanes_as_independent_jobs_with_a_final_gate() -> None:
    """Putting the lanes back into one job would restore the CI wall-clock bottleneck."""
    workflow = yaml.safe_load(GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert {"test-inventory", "test-migration", "test-backend", "test-worker", "test"}.issubset(jobs)
    inventory_step = next(
        step for step in jobs["test-inventory"]["steps"] if step["name"] == "Verify Python test inventory"
    )
    assert inventory_step["run"] == "uv run python scripts/ci/check_python_test_inventory.py"
    assert "postgres" in jobs["test-migration"]["services"]
    assert "redis" not in jobs["test-migration"]["services"]
    assert {"postgres", "redis"}.issubset(jobs["test-backend"]["services"])
    assert "services" not in jobs["test-worker"]
    assert set(jobs["test"]["needs"]) == {"test-inventory", "test-migration", "test-backend", "test-worker"}
    assert jobs["test"]["if"] == "${{ always() }}"

    final_steps = jobs["test"]["steps"]
    gate_step = final_steps[0]
    assert gate_step["name"] == "Verify Python test jobs succeeded"
    assert gate_step["env"] == {
        "INVENTORY_RESULT": "${{ needs.test-inventory.result }}",
        "MIGRATION_RESULT": "${{ needs.test-migration.result }}",
        "BACKEND_RESULT": "${{ needs.test-backend.result }}",
        "WORKER_RESULT": "${{ needs.test-worker.result }}",
    }
    for result_name in gate_step["env"]:
        assert f'"${result_name}" != "success"' in gate_step["run"]
    assert next(index for index, step in enumerate(final_steps) if step["name"] == "Coverage Report") > 0


def test_github_actions_combines_distinct_lane_coverage_artifacts() -> None:
    """Reusing one data file or omitting hidden files would lose coverage from a parallel lane."""
    workflow = yaml.safe_load(GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert jobs["test-backend"]["env"]["COVERAGE_FILE"] == ".coverage.backend"
    assert jobs["test-worker"]["env"]["COVERAGE_FILE"] == ".coverage.worker"

    backend_upload = next(
        step for step in jobs["test-backend"]["steps"] if step["name"] == "Upload Backend Coverage Data"
    )
    worker_upload = next(
        step for step in jobs["test-worker"]["steps"] if step["name"] == "Upload AI Worker Coverage Data"
    )

    assert backend_upload["uses"] == "actions/upload-artifact@v4"
    assert backend_upload["with"] == {
        "name": "python-coverage-backend",
        "path": ".coverage.backend",
        "include-hidden-files": True,
        "if-no-files-found": "error",
    }
    assert worker_upload["uses"] == "actions/upload-artifact@v4"
    assert worker_upload["with"] == {
        "name": "python-coverage-worker",
        "path": ".coverage.worker",
        "include-hidden-files": True,
        "if-no-files-found": "error",
    }

    final_steps = {step["name"]: step for step in jobs["test"]["steps"]}
    assert final_steps["Download Backend Coverage Data"]["with"]["name"] == "python-coverage-backend"
    assert final_steps["Download AI Worker Coverage Data"]["with"]["name"] == "python-coverage-worker"
    assert "coverage combine coverage-data/backend coverage-data/worker" in final_steps["Coverage Report"]["run"]
    assert "coverage report -m" in final_steps["Coverage Report"]["run"]


@pytest.mark.parametrize("target", REQUIRED_WORKER_INTEGRATION_TARGETS)
def test_run_test_script_includes_worker_recovery_integration_target(target: str) -> None:
    """GitHub Actions와 로컬 기본 runner가 같은 Worker 복구 경계를 검증해야 합니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")
    workflow = GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8")

    assert target in script
    assert target in workflow


def test_rag_integration_tests_run_in_local_runner_and_github_actions() -> None:
    """RAG integration 테스트 범위도 로컬 runner와 GitHub Actions가 같아야 합니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")
    workflow = GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8")

    assert "tests/integration/rag" in script
    assert "tests/integration/rag" in workflow


def test_example_local_env_storage_dir_is_a_container_path() -> None:
    """위 override가 필요한 이유를 고정합니다 — 이 값이 host 경로로 바뀌면 override 근거도 바뀝니다."""
    storage_dir = re.search(
        r"^STORAGE_DIR=(?P<value>.+)$",
        EXAMPLE_LOCAL_ENV.read_text(encoding="utf-8"),
        re.MULTILINE,
    )

    assert storage_dir is not None
    assert storage_dir.group("value").strip().startswith("/app/")
