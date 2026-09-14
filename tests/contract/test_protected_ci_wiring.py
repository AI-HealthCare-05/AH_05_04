"""protected 제한 로그인 검증이 CI에 실제로 배선되어 있는지 고정합니다.

`tests/migration/test_protected_retrieval_migration.py`의 `protected_database` fixture는
`PROTECTED_TEST_DATABASE_URL`이 없으면 테스트를 건너뜁니다. 이 변수가 CI에 배선된 적이
없어 protected 권한 negative test 30건이 매 실행 skip됐고, 정적 검사만 통과하고 있었습니다(#473).

PD-368 §4는 권한 경계를 "실제 제한 로그인·동시성·rollback 통합 테스트로 입증"하도록 요구하고
정적 검색만으로 판정하지 않는다고 명시합니다. 증빙(`PROTECTED_LIMITED_LOGIN_CI: PASSED`)은
builder에 박힌 고정 문자열이므로, 배선이 사라지면 그 기록이 조용히 거짓이 됩니다.

아래 테스트는 배선 자체를 단언해 증빙을 구조적으로 뒷받침합니다. 단계 이름에 결속하면 이름
변경만으로 깨지므로 잡 단위로 확인합니다.
"""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GITHUB_ACTIONS_CHECKS = PROJECT_ROOT / ".github" / "workflows" / "checks.yml"
PROTECTED_MIGRATION_TEST = PROJECT_ROOT / "tests" / "migration" / "test_protected_retrieval_migration.py"

PROTECTED_DATABASE_URL = "PROTECTED_TEST_DATABASE_URL"
PROTECTED_DATABASE_NAME = "protected368_test"

# 이 두 잡이 protected_database fixture를 쓰는 테스트를 수집합니다.
# test-backend는 tests/integration/rag의 두 파일이 pytest_plugins로 같은 fixture를 공유합니다.
REQUIRED_JOBS = ("test-migration", "test-backend")


def _jobs() -> dict[str, Any]:
    return dict(yaml.safe_load(GITHUB_ACTIONS_CHECKS.read_text(encoding="utf-8"))["jobs"])


def _steps(job_name: str) -> list[dict[str, Any]]:
    job = _jobs().get(job_name)
    assert job is not None, f"{job_name} 잡이 checks.yml에 없습니다."
    return [step for step in job.get("steps", []) if isinstance(step, dict)]


def test_protected_database_url_is_injected_in_every_job_that_needs_it() -> None:
    for job_name in REQUIRED_JOBS:
        injected = [step for step in _steps(job_name) if PROTECTED_DATABASE_URL in (step.get("env") or {})]

        assert injected, (
            f"{job_name} 잡에 {PROTECTED_DATABASE_URL}이 주입되지 않았습니다. "
            "주입이 빠지면 protected 제한 로그인 검증이 조용히 skip됩니다."
        )


def test_injected_protected_database_is_created_in_the_same_job() -> None:
    """주입한 URL이 같은 잡에서 만드는 DB를 가리켜야 합니다. 공용 test DB와 분리된 상태를 고정합니다."""
    for job_name in REQUIRED_JOBS:
        steps = _steps(job_name)
        urls = [
            str(step["env"][PROTECTED_DATABASE_URL])
            for step in steps
            if PROTECTED_DATABASE_URL in (step.get("env") or {})
        ]
        created = any(f"CREATE DATABASE {PROTECTED_DATABASE_NAME}" in str(step.get("run", "")) for step in steps)

        assert created, f"{job_name} 잡이 {PROTECTED_DATABASE_NAME}를 만들지 않습니다."
        for url in urls:
            assert url.endswith(f"/{PROTECTED_DATABASE_NAME}"), (
                f"{job_name} 잡의 {PROTECTED_DATABASE_URL}이 {PROTECTED_DATABASE_NAME}를 가리키지 않습니다: {url}"
            )


def test_fixture_fails_closed_in_ci_instead_of_skipping() -> None:
    """배선이 빠졌을 때 CI가 먼저 깨지도록 하는 장치가 유지되는지 확인합니다."""
    source = PROTECTED_MIGRATION_TEST.read_text(encoding="utf-8")

    assert 'os.getenv("CI")' in source
    assert f'pytest.fail("{PROTECTED_DATABASE_URL} is required in CI")' in source
