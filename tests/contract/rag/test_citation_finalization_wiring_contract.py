from __future__ import annotations

import ast
from pathlib import Path

MODULE_PATH = Path("ai_worker/tasks/rag/citation_finalization_wiring.py")


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


def test_wiring_consumes_authority_outcome_and_reuses_existing_finalizer() -> None:
    modules, names = _imports()

    assert "CitationAuthorityIssueOutcome" in names["ai_worker.tasks.rag.citation_authorization_authority"]
    assert "finalize_citations" in names["ai_worker.tasks.rag.citation_finalizer"]


def test_wiring_does_not_take_authority_issuance_or_persistence_ownership() -> None:
    modules, names = _imports()
    imported_names = {name for module_names in names.values() for name in module_names}

    assert not any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in modules)
    assert not any(module == "backend.app" or module.startswith("backend.app.") for module in modules)
    assert "issue_citation_authorization" not in imported_names
    assert "CitationAuthorityStorePort" not in imported_names
    assert not any("reader" in name.lower() for name in imported_names)
