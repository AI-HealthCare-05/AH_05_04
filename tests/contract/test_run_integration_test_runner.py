"""단일 통합테스트 runner의 범위와 fail-closed guard를 검증합니다."""

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER = PROJECT_ROOT / "scripts" / "ci" / "run_integration_test.sh"
TEST_ENVIRONMENT = PROJECT_ROOT / "scripts" / "ci" / "test_environment.sh"


def _run_with_environment(*, env_file: Path, compose_file: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ENV_FILE": str(env_file),
            "COMPOSE_FILE": str(compose_file),
        }
    )
    return subprocess.run(
        ["bash", str(RUNNER)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


def test_runner_executes_the_complete_integration_directory() -> None:
    script = RUNNER.read_text(encoding="utf-8")

    assert "ENVIRONMENT_ERROR_EXIT_CODE=2" in script
    assert "run_with_integration_test_environment pytest tests/integration -q" in script
    assert "tests/integration/test_" not in script
    assert "[TEST FAILURE]" in script
    assert "[SUCCESS]" in script


def test_runner_uses_shared_safe_environment_setup() -> None:
    script = RUNNER.read_text(encoding="utf-8")

    assert "source scripts/ci/test_environment.sh" in script
    assert "prepare_test_environment" in script


def test_environment_setup_checks_both_service_readiness_and_host_ports() -> None:
    script = TEST_ENVIRONMENT.read_text(encoding="utf-8")

    assert "pg_isready" in script
    assert "redis-cli ping" in script
    assert "port postgres 5432" in script
    assert "port redis 6379" in script
    assert "TEST_SERVICE_READY_ATTEMPTS=15" in script
    assert "TEST_SERVICE_READY_INTERVAL_SECONDS=2" in script
    assert "wait_for_postgres" in script
    assert "wait_for_redis" in script


@pytest.mark.parametrize("unsafe_name", ["production.env", "docker-compose.prod.yml"])
def test_runner_rejects_production_named_configuration_before_docker(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    env_file = tmp_path / (unsafe_name if unsafe_name.endswith(".env") else "local.env")
    compose_file = tmp_path / (unsafe_name if unsafe_name.endswith(".yml") else "compose.yml")
    env_file.write_text("ENV=local\n", encoding="utf-8")
    compose_file.write_text("services: {}\n", encoding="utf-8")

    result = _run_with_environment(env_file=env_file, compose_file=compose_file)

    assert result.returncode == 2
    assert "[ENVIRONMENT ERROR]" in result.stdout
    assert "Production" in result.stdout


def test_runner_rejects_non_local_environment_before_docker(tmp_path: Path) -> None:
    env_file = tmp_path / "staging.env"
    compose_file = tmp_path / "compose.yml"
    env_file.write_text("ENV=staging\n", encoding="utf-8")
    compose_file.write_text("services: {}\n", encoding="utf-8")

    result = _run_with_environment(env_file=env_file, compose_file=compose_file)

    assert result.returncode == 2
    assert "[ENVIRONMENT ERROR]" in result.stdout
    assert "local 또는 test" in result.stdout
