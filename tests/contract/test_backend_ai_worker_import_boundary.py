"""`backend` production 코드가 import할 수 있는 `ai_worker` 모듈을 허용 목록으로 고정합니다.

`PD-175-20260910`이 (A)안 — `backend`가 `ai_worker`의 **순수 kernel 모듈 하나만** production
import한다 — 을 확정했고, `PD-168-20260915`가 RAG-07B Candidate Index build의 2개 순수 모듈을
추가 승인했습니다.

`PD-800-20260919`는 Candidate Index Builder one-shot 스크립트(`scripts/candidate_index_builder.py`)에
대해 `app-${APP_VERSION}` 배포 이미지 내부에서 필요한 최소 adapter import를 추가 승인했습니다.

이 경계는 코드로 강제되지 않으면 조용히 넓어지므로, 허용 목록 밖의 import를 계약 테스트로 막습니다.
특히 Dockerfile이 `app` 이미지에 COPY하는 production scripts 표면도 계약 스캔에 결속하여,
파일 위치 이동으로 경계를 우회하지 못하도록 방어합니다.

역방향(`ai_worker` → `backend`)은 별도 수단으로 이미 강제됩니다. CI Worker lane이
`backend`를 PYTHONPATH에서 제외한 별도 프로세스로 `ai_worker` 테스트를 실행합니다
(`scripts/ci/test_environment.sh::run_with_worker_test_environment`).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 배포 이미지에 들어가는 backend 소스 전체를 본다. `backend/app/Dockerfile`이
# `COPY ./backend/app ./app`과 `COPY ./backend/alembic ./alembic`을 모두 수행하므로,
# migration도 production 표면의 일부다.
BACKEND_PRODUCTION_ROOTS = (
    PROJECT_ROOT / "backend" / "app",
    PROJECT_ROOT / "backend" / "alembic",
)
PRODUCTION_ROOTS = BACKEND_PRODUCTION_ROOTS

# 테스트 코드는 경계 대상이 아닙니다.
TEST_DIRECTORY_NAMES = {"tests", "tests_unit"}

# Backend production packages (backend/app/**, backend/alembic/**) pure-kernel allowlist.
# 이 목록은 PD-175 및 PD-168의 순수 모듈 원칙을 유지하며, SQLAlchemy 어댑터를 포함하지 않습니다.
BACKEND_ALLOWED_AI_WORKER_MODULES = frozenset(
    {
        # PD-175-20260910 (A)안: RAG-12A Runtime Bundle build kernel.
        "ai_worker.tasks.rag.runtime_bundle_builder",
        # PD-168-20260915: RAG-07B Candidate Index build 진입점과 그 필수 파라미터 타입.
        "ai_worker.tasks.rag.candidate_index",
        "ai_worker.tasks.rag.catalog.export",
    }
)
ALLOWED_AI_WORKER_MODULES = BACKEND_ALLOWED_AI_WORKER_MODULES

# CLOSED_DEMO #180 Decision: one exact composition file may consume the
# pre-existing retrieval components needed for the sealed 17-product demo.
# This is deliberately an equality-checked exception, not a global expansion.
CLOSED_DEMO_BACKEND_AI_WORKER_MODULES: dict[str, frozenset[str]] = {
    "backend/app/core/closed_demo_retrieval.py": frozenset(
        {
            "ai_worker.adapters.openai_text_embedding",
            "ai_worker.adapters.postgresql_evidence_eligibility",
            "ai_worker.adapters.postgresql_evidence_search",
            "ai_worker.adapters.sqlalchemy_knowledge_chunk_content",
            "ai_worker.tasks.rag.closed_demo_retrieval_binding",
            "ai_worker.tasks.rag.evidence_retrieval",
            "ai_worker.tasks.rag.evidence_search",
            "ai_worker.tasks.rag.production_evidence_gate",
            "ai_worker.tasks.rag.retrieval_runtime",
        }
    )
}

# app-${APP_VERSION} 이미지에 COPY되는 production script별 허용 목록.
# 명시되지 않은 스크립트의 기본 허용값은 frozenset() (import 금지)입니다.
APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES: dict[str, frozenset[str]] = {
    # PD-800-20260919: Candidate Index Builder one-shot operator command
    "scripts/candidate_index_builder.py": frozenset(
        {
            "ai_worker.adapters.sqlalchemy_catalog_approval_verifier",
            "ai_worker.adapters.sqlalchemy_catalog_write_support",
            "ai_worker.tasks.rag.candidate_index",
        }
    ),
    # #178 RET-H synthetic smoke one-shot validation script
    "scripts/ret_h_aws_synthetic_smoke.py": frozenset(
        {
            "ai_worker.adapters.postgresql_evidence_search",
            "ai_worker.core",
            "ai_worker.tasks.evaluation.ret_h_smoke",
            "ai_worker.tasks.rag.evidence_retrieval",
            "ai_worker.tasks.rag.evidence_search",
            "ai_worker.tasks.rag.retrieval_runtime",
        }
    ),
}


def _production_python_files() -> list[Path]:
    return [
        path
        for root in BACKEND_PRODUCTION_ROOTS
        for path in root.rglob("*.py")
        if not TEST_DIRECTORY_NAMES.intersection(path.relative_to(root).parts)
    ]


def _dockerfile_copied_python_scripts() -> tuple[Path, ...]:
    """Parse backend/app/Dockerfile fail-closed to find all repository scripts copied to the app image."""
    dockerfile_path = PROJECT_ROOT / "backend" / "app" / "Dockerfile"
    copied_scripts: list[Path] = []
    for line_number, raw_line in enumerate(dockerfile_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("COPY ") and "./scripts" in line:
            parts = line.split()
            if len(parts) != 3:
                raise ValueError(f"Unhandled COPY syntax in {dockerfile_path}:{line_number}: {raw_line}")
            src = parts[1]
            if not src.startswith("./scripts/"):
                raise ValueError(f"Unexpected scripts COPY source in {dockerfile_path}:{line_number}: {raw_line}")
            script_path = PROJECT_ROOT / src.removeprefix("./")
            if not script_path.name.endswith(".py"):
                raise ValueError(f"Non-python script COPY in {dockerfile_path}:{line_number}: {raw_line}")
            if not script_path.is_file():
                raise FileNotFoundError(f"Dockerfile references non-existent script: {script_path}")
            copied_scripts.append(script_path)
    if not copied_scripts:
        raise ValueError(f"No scripts COPY found in {dockerfile_path}")
    return tuple(copied_scripts)


def _imported_ai_worker_modules(path: Path) -> set[str]:
    """실제 import 문만 센다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.split(".")[0] == "ai_worker")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".")[0] == "ai_worker":
                modules.add(node.module)
    return modules


def test_backend_production_code_imports_only_allowed_ai_worker_modules() -> None:
    violations: dict[str, set[str]] = {}
    for path in _production_python_files():
        relative_path = str(path.relative_to(PROJECT_ROOT))
        actual = _imported_ai_worker_modules(path)
        if relative_path in CLOSED_DEMO_BACKEND_AI_WORKER_MODULES:
            assert actual == CLOSED_DEMO_BACKEND_AI_WORKER_MODULES[relative_path]
            continue
        disallowed = actual - BACKEND_ALLOWED_AI_WORKER_MODULES
        if disallowed:
            violations[str(path.relative_to(PROJECT_ROOT))] = disallowed

    assert violations == {}, (
        "허용 목록 밖의 ai_worker 모듈을 backend production 코드가 import합니다. "
        "경계를 넓히려면 PD-175 후속 Decision과 함께 BACKEND_ALLOWED_AI_WORKER_MODULES를 갱신하세요: "
        f"{ {file: sorted(modules) for file, modules in sorted(violations.items())} }"
    )


def test_app_image_production_scripts_import_only_authorized_ai_worker_modules() -> None:
    """app 이미지에 COPY되는 production script의 import를 검증합니다."""
    violations: dict[str, set[str]] = {}
    for path in _dockerfile_copied_python_scripts():
        rel_path = str(path.relative_to(PROJECT_ROOT))
        allowed = APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES.get(rel_path, frozenset())
        disallowed = _imported_ai_worker_modules(path) - allowed
        if disallowed:
            violations[rel_path] = disallowed

    assert violations == {}, (
        "app 이미지에 COPY되는 스크립트가 승인되지 않은 ai_worker 모듈을 import합니다. "
        "정식 Decision 승인 및 APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES 갱신이 필요합니다: "
        f"{ {file: sorted(modules) for file, modules in sorted(violations.items())} }"
    )


def test_app_image_production_scripts_are_audited_and_governed() -> None:
    """Dockerfile COPY 스크립트와 contract scan의 자동 결속 및 우회 방지(anti-evasion)를 검증합니다."""
    candidate_script = PROJECT_ROOT / "scripts" / "candidate_index_builder.py"
    assert candidate_script.is_file(), f"Candidate builder script not found at {candidate_script}"

    copied_scripts = _dockerfile_copied_python_scripts()
    assert candidate_script in copied_scripts, (
        f"{candidate_script.relative_to(PROJECT_ROOT)} must be explicitly COPY-ed in backend/app/Dockerfile"
    )

    # Candidate Builder AST import exact-match check
    candidate_rel = "scripts/candidate_index_builder.py"
    assert candidate_rel in APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES
    actual_candidate_imports = _imported_ai_worker_modules(candidate_script)
    assert actual_candidate_imports == APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES[candidate_rel], (
        f"Candidate builder imports {actual_candidate_imports} do not match exact allowlist "
        f"{APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES[candidate_rel]}"
    )

    # Ensure no other app image script can import catalog approval verifier or write support adapters
    catalog_adapters = {
        "ai_worker.adapters.sqlalchemy_catalog_approval_verifier",
        "ai_worker.adapters.sqlalchemy_catalog_write_support",
    }
    for script_path in copied_scripts:
        if script_path == candidate_script:
            continue
        other_imports = _imported_ai_worker_modules(script_path)
        assert not other_imports.intersection(catalog_adapters), (
            f"Script {script_path.relative_to(PROJECT_ROOT)} must not import catalog adapters {catalog_adapters}"
        )


def test_allowed_modules_exist_so_the_allowlist_cannot_rot() -> None:
    """허용 목록의 모듈이 실제로 존재해야 한다 — 이름이 바뀌면 목록이 조용히 무의미해진다."""
    all_allowed = set(BACKEND_ALLOWED_AI_WORKER_MODULES)
    for script_allowed in APP_IMAGE_SCRIPT_ALLOWED_AI_WORKER_MODULES.values():
        all_allowed.update(script_allowed)

    for module in sorted(all_allowed):
        parts = module.split(".")
        py_file = PROJECT_ROOT.joinpath(*parts).with_suffix(".py")
        init_file = PROJECT_ROOT.joinpath(*parts) / "__init__.py"
        assert py_file.is_file() or init_file.is_file(), (
            f"허용 목록의 {module} 이 존재하지 않습니다 ({py_file} or {init_file})"
        )


def test_production_scan_covers_every_deployed_backend_root() -> None:
    """스캔 대상이 비거나 한쪽 루트를 빠뜨리면 위 테스트들이 조용히 통과한다."""
    files = _production_python_files()

    assert len(files) > 50, len(files)
    for root in BACKEND_PRODUCTION_ROOTS:
        assert root.is_dir(), root
        assert any(root in path.parents for path in files), root
        assert not any(
            TEST_DIRECTORY_NAMES.intersection(path.relative_to(root).parts) for path in files if root in path.parents
        ), root
