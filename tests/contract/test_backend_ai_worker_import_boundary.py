"""`backend` production 코드가 import할 수 있는 `ai_worker` 모듈을 허용 목록으로 고정합니다.

`PD-175-20260910`이 (A)안 — `backend`가 `ai_worker`의 **순수 kernel 모듈 하나만** production
import한다 — 을 확정했습니다. 이 경계는 코드로 강제되지 않으면 조용히 넓어지므로, 허용 목록
밖의 import를 계약 테스트로 막습니다. PR #416 리뷰에서 두 리뷰어가 함께 요청한 후속 항목입니다.

역방향(`ai_worker` → `backend`)은 별도 수단으로 이미 강제됩니다. CI Worker lane이
`backend`를 PYTHONPATH에서 제외한 별도 프로세스로 `ai_worker` 테스트를 실행합니다
(`scripts/ci/test_environment.sh::run_with_worker_test_environment`).

한계. 이 테스트는 **직접 import 문**(`import x`, `from x import y`)만 봅니다.
`importlib.import_module`·`__import__`처럼 런타임에 문자열로 모듈을 불러오는 경로는
탐지하지 않으므로, 배포 표면 전체를 보장하지는 않습니다. 경계를 넓히려는 변경은 대개
직접 import로 들어오므로 이 범위에서 실질적인 방어가 됩니다.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 배포 이미지에 들어가는 backend 소스 전체를 본다. `backend/app/Dockerfile`이
# `COPY ./backend/app ./app`과 `COPY ./backend/alembic ./alembic`을 모두 수행하므로,
# migration도 production 표면의 일부다. app만 스캔하면 어떤 migration이 ai_worker를
# import해도 이 테스트가 통과해버린다.
PRODUCTION_ROOTS = (
    PROJECT_ROOT / "backend" / "app",
    PROJECT_ROOT / "backend" / "alembic",
)

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
        for root in PRODUCTION_ROOTS
        for path in root.rglob("*.py")
        if not TEST_DIRECTORY_NAMES.intersection(path.relative_to(root).parts)
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


def test_production_scan_covers_every_deployed_backend_root() -> None:
    """스캔 대상이 비거나 한쪽 루트를 빠뜨리면 위 두 테스트가 조용히 통과한다.

    루트별로 파일이 실제 잡히는지 확인한다. `backend/alembic` 누락이 PR #440 리뷰에서
    지적된 실제 결함이었다.
    """
    files = _production_python_files()

    assert len(files) > 50, len(files)
    for root in PRODUCTION_ROOTS:
        assert root.is_dir(), root
        assert any(root in path.parents for path in files), root
        assert not any(
            TEST_DIRECTORY_NAMES.intersection(path.relative_to(root).parts) for path in files if root in path.parents
        ), root
