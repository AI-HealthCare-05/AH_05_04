"""`scripts/ci/run_test.sh`가 컨테이너용 설정을 host 테스트에 흘리지 않는지 확인합니다.

`run_test.sh`는 `uv run --env-file "$ENV_FILE"`로 `envs/.local.env` 전체를 주입합니다.
그 파일은 컨테이너용이라 `STORAGE_DIR`이 컨테이너 절대경로이고, local live 검증 절차
(`docs/validation/ai-one-cycle-release.md`)는 `RELEASE_VALIDATION_ALLOWED`와
`OCR_STRUCTURE_LLM_ENABLED`를 켜두도록 안내합니다. 이 값들이 host 테스트로 새어 들어가면
실제 결함이 아닌 연쇄 실패가 발생합니다(IT-1 QA 2026-09-02, 25건).

uv는 shell 환경변수를 `--env-file`보다 우선 적용하므로 `env VAR=... uv run` 방식으로
덮어쓸 수 있습니다. 아래 테스트는 그 override 목록이 유지되는지 고정합니다.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_TEST_SCRIPT = PROJECT_ROOT / "scripts" / "ci" / "run_test.sh"
EXAMPLE_LOCAL_ENV = PROJECT_ROOT / "envs" / "example.local.env"

# ENV_FILE에서 값을 물려받으면 host 테스트가 깨지는 설정과, run_test.sh가 강제해야 하는 값입니다.
CONTAINER_ONLY_SETTINGS = {
    "RELEASE_VALIDATION_ALLOWED": "false",
    "OCR_STRUCTURE_LLM_ENABLED": "false",
}


def _run_with_test_database_body() -> str:
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")
    body = re.search(r"run_with_test_database\(\)\s*\{(?P<body>.*?)\n\}", script, re.DOTALL)

    assert body is not None, "run_with_test_database() 함수를 찾지 못했습니다."

    return body.group("body")


@pytest.mark.parametrize(("name", "expected"), sorted(CONTAINER_ONLY_SETTINGS.items()))
def test_run_test_script_forces_test_value_for_container_only_setting(name: str, expected: str) -> None:
    assert f"{name}={expected}" in _run_with_test_database_body(), (
        f"{name}을(를) test 기준값으로 덮어쓰지 않으면 envs/.local.env의 현재 값이 host 테스트에 적용됩니다."
    )


def test_run_test_script_replaces_container_storage_dir_with_host_directory() -> None:
    """`STORAGE_DIR`은 고정 문자열이 아니라 host 임시 디렉터리로 덮어써야 합니다."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")

    assert 'STORAGE_DIR="$TEST_STORAGE_DIR"' in _run_with_test_database_body()
    assert 'TEST_STORAGE_DIR="$(mktemp -d)"' in script
    assert "trap 'rm -rf \"$TEST_STORAGE_DIR\"' EXIT" in script


def test_run_test_script_exposes_backend_and_shared_contract_packages() -> None:
    """Console entry points must import both `app` and root-level shared contracts."""
    script = RUN_TEST_SCRIPT.read_text(encoding="utf-8")

    assert 'REPOSITORY_ROOT="$(pwd)"' in script
    assert 'PYTHONPATH="$REPOSITORY_ROOT/backend:$REPOSITORY_ROOT"' in _run_with_test_database_body()


def test_example_local_env_storage_dir_is_a_container_path() -> None:
    """위 override가 필요한 이유를 고정합니다 — 이 값이 host 경로로 바뀌면 override 근거도 바뀝니다."""
    storage_dir = re.search(
        r"^STORAGE_DIR=(?P<value>.+)$",
        EXAMPLE_LOCAL_ENV.read_text(encoding="utf-8"),
        re.MULTILINE,
    )

    assert storage_dir is not None
    assert storage_dir.group("value").strip().startswith("/app/")
