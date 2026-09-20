from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from ai_worker.tasks.rag.guide_citation_runtime_orchestration import (
    GuideCitationRuntimeOrchestrationRequest,
)

MODULE_PATH = Path("ai_worker/tasks/rag/guide_citation_runtime_orchestration.py")


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


def test_request_accepts_only_generation_lookup_coordinate_and_time() -> None:
    assert tuple(field.name for field in fields(GuideCitationRuntimeOrchestrationRequest)) == (
        "generation_request",
        "request_guard_runtime_binding_ref",
        "evaluation_time",
    )


def test_orchestration_reuses_existing_application_and_authority_functions() -> None:
    _, names = _imports()

    assert "orchestrate_guide_generation_card" in names["ai_worker.tasks.rag.guide_generation_card_orchestration"]
    assert "run_guide_claim_citation_validation" in names["ai_worker.tasks.rag.guide_claim_citation_validation"]
    assert "build_citation_authorization_request" in names["ai_worker.tasks.rag.citation_authorization"]
    assert "issue_citation_authorization" in names["ai_worker.tasks.rag.citation_authorization_authority"]
    assert "finalize_citation_authority_outcome" in names["ai_worker.tasks.rag.citation_finalization_wiring"]


def test_orchestration_does_not_take_infrastructure_policy_or_runtime_graph_ownership() -> None:
    modules, _ = _imports()
    forbidden_roots = ("sqlalchemy", "backend.app", "langgraph")

    assert not any(module == root or module.startswith(f"{root}.") for module in modules for root in forbidden_roots)
    assert not any("worker" in module.lower() and "ai_worker.tasks.rag" not in module for module in modules)
