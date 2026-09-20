from __future__ import annotations

import ast
import inspect

from ai_worker.tasks.rag import guide_runtime_release
from ai_worker.tasks.rag.guide_runtime_release import (
    GuideRuntimeEvidenceStatus,
    GuideRuntimeExecutionStatus,
    GuideRuntimeReleaseDecision,
)
from ai_worker.tasks.rag.guideline_card import GuidelineFallbackCode


def test_runtime_release_vocabulary_matches_safety_result_v2() -> None:
    assert {item.value for item in GuideRuntimeExecutionStatus} == {
        "SUCCEEDED",
        "NO_RESULT",
        "TIMED_OUT",
        "DEPENDENCY_ERROR",
        "VALIDATION_ERROR",
    }
    assert {item.value for item in GuideRuntimeEvidenceStatus} == {
        "SUFFICIENT",
        "INSUFFICIENT",
        "CONFLICTED",
        "STALE",
    }
    assert {item.value for item in GuideRuntimeReleaseDecision} == {
        "PASS",
        "LIMITED",
        "REJECTED",
        "STALE",
    }
    assert {item.value for item in GuidelineFallbackCode} == {
        "NO_APPROVED_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "PROVIDER_TIMEOUT",
        "DEPENDENCY_UNAVAILABLE",
        "VALIDATION_FAILED",
        "PRESCRIPTION_STALE",
        "EXECUTION_CONTEXT_STALE",
        "UNSUPPORTED_REQUEST",
    }


def test_runtime_release_kernel_has_no_forbidden_runtime_dependencies() -> None:
    tree = ast.parse(inspect.getsource(guide_runtime_release))
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    )

    assert not any(module.startswith("ai_worker.tasks.evaluation") for module in imported_modules)
    assert not any(module.startswith("backend.app") for module in imported_modules)
    assert not any(module.startswith("sqlalchemy") for module in imported_modules)
    assert not any(module.startswith("langgraph") for module in imported_modules)
    assert not any(module.startswith(("httpx", "requests", "urllib")) for module in imported_modules)
