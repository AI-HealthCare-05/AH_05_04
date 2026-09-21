import ast
from pathlib import Path

MODULE_PATH = Path("ai_worker/tasks/rag/guide_runtime_composition.py")


def _imports() -> tuple[set[str], dict[str, set[str]]]:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    modules: set[str] = set()
    names: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            names.setdefault(node.module, set()).update(alias.name for alias in node.names)
    return modules, names


def test_composition_reuses_existing_citation_release_and_projection_boundaries() -> None:
    _, names = _imports()

    assert "orchestrate_guide_citation_runtime" in names["ai_worker.tasks.rag.guide_citation_runtime_orchestration"]
    assert "finalize_guide_runtime_release" in names["ai_worker.tasks.rag.guide_runtime_release"]
    assert "project_guide_runtime_release" in names["ai_worker.tasks.rag.guide_release_projection"]
    assert "GuideRuntimeReleaseProjectionOutcome" in names["rag_runtime.guide_release_projection"]


def test_composition_has_no_backend_database_or_runtime_graph_imports() -> None:
    modules, _ = _imports()
    forbidden_roots = ("backend", "app", "sqlalchemy", "langgraph")

    assert not any(module == root or module.startswith(f"{root}.") for module in modules for root in forbidden_roots)
