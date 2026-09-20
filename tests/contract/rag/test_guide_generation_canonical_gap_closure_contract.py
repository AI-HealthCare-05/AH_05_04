from __future__ import annotations

import importlib
from pathlib import Path

GAP_CLOSURE_PATH = Path("docs/contracts/targets/post-mvp-1/guide-generation-canonical-gap-closure-v1.md")
READINESS_PATH = Path("docs/contracts/targets/post-mvp-1/guide-langgraph-callable-readiness-v1.md")

CANONICAL_NODES = (
    "select_medication_guidelines",
    "medication_guideline_safety_filter",
    "conflict_gate",
    "compose_personalized_guide",
)


def test_gap_closure_audits_the_exact_four_canonical_nodes() -> None:
    content = GAP_CLOSURE_PATH.read_text(encoding="utf-8")

    assert tuple(
        line.removeprefix("### `").removesuffix("`") for line in content.splitlines() if line.startswith("### `")
    ) == (
        "four-node-semantic-matrix",
        *CANONICAL_NODES,
    )


def test_gap_closure_freezes_truthful_non_mappings() -> None:
    content = GAP_CLOSURE_PATH.read_text(encoding="utf-8")

    for node_id, classification in (
        ("select_medication_guidelines", "CONTRACT_ALIGNMENT_REQUIRED"),
        ("medication_guideline_safety_filter", "CONTRACT_ALIGNMENT_REQUIRED"),
        ("conflict_gate", "CONTRACT_ALIGNMENT_REQUIRED"),
        ("compose_personalized_guide", "THIN_EXTRACTION"),
    ):
        row = next(line for line in content.splitlines() if line.startswith(f"| {node_id} |"))
        assert f"| {classification} |" in row

    assert "project_guideline_evidence_from_handoff != select_medication_guidelines" in content
    assert "post-generation Card validation != medication_guideline_safety_filter" in content
    assert "EVIDENCE_CONFLICTED != conflict_gate" in content
    assert "orchestrate_guide_generation_card != compose_personalized_guide" in content


def test_compose_node_is_the_only_new_importable_callable() -> None:
    composer = importlib.import_module("ai_worker.tasks.rag.guide_personalized_composition")

    assert callable(composer.compose_personalized_guide)
    assert "guide_medication_guideline_selection" not in {
        module.stem for module in Path("ai_worker/tasks/rag").glob("*.py")
    }
    assert "guide_medication_safety_filter" not in {module.stem for module in Path("ai_worker/tasks/rag").glob("*.py")}
    assert "guide_conflict_gate" not in {module.stem for module in Path("ai_worker/tasks/rag").glob("*.py")}


def test_readiness_marks_only_the_composition_node_as_a_thin_adapter() -> None:
    content = READINESS_PATH.read_text(encoding="utf-8")

    assert "| status | `THIN_ADAPTER_NEEDED` |" in content
    assert "ai_worker.tasks.rag.guide_personalized_composition:compose_personalized_guide" in content
    assert "THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS" in content
