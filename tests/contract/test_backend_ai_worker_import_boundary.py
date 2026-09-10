"""`backend` production 코드가 import할 수 있는 `ai_worker` 모듈을 허용 목록으로 고정합니다.

`PD-175-20260910`이 (A)안 — `backend`가 `ai_worker`의 **순수 kernel 모듈 하나만** production
import한다 — 을 확정했습니다. 이 경계는 코드로 강제되지 않으면 조용히 넓어지므로, 허용 목록
밖의 import를 계약 테스트로 막습니다. PR #416 리뷰에서 두 리뷰어가 함께 요청한 후속 항목입니다.

역방향(`ai_worker` → `backend`)은 별도 수단으로 이미 강제됩니다. CI Worker lane이
`backend`를 PYTHONPATH에서 제외한 별도 프로세스로 `ai_worker` 테스트를 실행합니다
(`scripts/ci/test_environment.sh::run_with_worker_test_environment`).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = PROJECT_ROOT / "backend" / "app"

# 테스트 코드는 경계 대상이 아닙니다. `test_async_job_schema.py`처럼 두 패키지의 계약을
# 맞대어 검증하는 테스트는 의도적으로 양쪽을 import합니다.
TEST_DIRECTORY_NAMES = {"tests", "tests_unit"}

ALLOWED_AI_WORKER_MODULES = frozenset(
    {
        # PD-175-20260910 (A)안: RAG-12A Runtime Bundle build kernel.
        # I/O·시계·session이 없는 순수 모듈이며, backend가 이미 import하는
        # `provider_contracts`와 같은 형태입니다.
        "ai_worker.tasks.rag.runtime_bundle_builder",
    }
)


def _production_python_files() -> list[Path]:
    return [
        path
        for path in BACKEND_APP.rglob("*.py")
        if not TEST_DIRECTORY_NAMES.intersection(path.relative_to(BACKEND_APP).parts)
    ]


def _imported_ai_worker_modules(path: Path) -> set[str]:
    """실제 import 문만 센다.

    문자열 리터럴에 `ai_worker`가 들어간 경우(logger 이름 등)를 세지 않으려면 grep이 아니라
    AST를 봐야 합니다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.split(".")[0] == "ai_worker")
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` 같은 상대 import는 module이 None이거나 level > 0입니다.
            if node.level == 0 and node.module and node.module.split(".")[0] == "ai_worker":
                modules.add(node.module)
    return modules


def test_backend_production_code_imports_only_allowed_ai_worker_modules() -> None:
    violations: dict[str, set[str]] = {}
    for path in _production_python_files():
        disallowed = _imported_ai_worker_modules(path) - ALLOWED_AI_WORKER_MODULES
        if disallowed:
            violations[str(path.relative_to(PROJECT_ROOT))] = disallowed

    assert violations == {}, (
        "허용 목록 밖의 ai_worker 모듈을 backend production 코드가 import합니다. "
        "경계를 넓히려면 PD-175 후속 Decision과 함께 ALLOWED_AI_WORKER_MODULES를 갱신하세요: "
        f"{ {file: sorted(modules) for file, modules in sorted(violations.items())} }"
    )


def test_allowed_modules_exist_so_the_allowlist_cannot_rot() -> None:
    """허용 목록의 모듈이 실제로 존재해야 한다 — 이름이 바뀌면 목록이 조용히 무의미해진다."""
    for module in sorted(ALLOWED_AI_WORKER_MODULES):
        path = PROJECT_ROOT.joinpath(*module.split(".")).with_suffix(".py")
        assert path.is_file(), f"허용 목록의 {module} 이 존재하지 않습니다 ({path})"


def test_production_scan_actually_covers_backend_app() -> None:
    """스캔 대상이 비어 있으면 위 두 테스트가 조용히 통과한다."""
    files = _production_python_files()

    assert len(files) > 50, len(files)
    assert not any("tests" in path.relative_to(BACKEND_APP).parts for path in files)
