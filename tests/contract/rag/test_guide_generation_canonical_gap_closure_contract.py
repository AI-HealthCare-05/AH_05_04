from __future__ import annotations

import importlib
from pathlib import Path

GAP_CLOSURE_PATH = Path("docs/contracts/targets/post-mvp-1/guide-generation-canonical-gap-closure-v1.md")
READINESS_PATH = Path("docs/contracts/targets/post-mvp-1/guide-langgraph-callable-readiness-v1.md")

CANONICAL_NODES = (
    "compose_personalized_guide",
    "medication_guideline_safety_filter",
)

TOPOLOGY_DECISIONS = {
    "validate_bundle_and_source_freshness": "RETAIN_STANDALONE",
    "product_safety_overlay_gate_if_bundle_capability_enabled": "RETIRE_FROM_CURRENT_MVP_TOPOLOGY",
    "select_medication_guidelines": "MERGE_INTO_EXISTING_BOUNDARY",
    "medication_guideline_safety_filter": "RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION",
    "conflict_gate": "MERGE_INTO_EXISTING_BOUNDARY",
}
VALID_TOPOLOGY_DECISIONS = {
    "RETAIN_STANDALONE",
    "MERGE_INTO_EXISTING_BOUNDARY",
    "RETIRE_FROM_CURRENT_MVP_TOPOLOGY",
    "RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION",
}


def test_gap_closure_records_the_exact_reduced_canonical_node_set() -> None:
    content = GAP_CLOSURE_PATH.read_text(encoding="utf-8")

    assert tuple(
        line.removeprefix("### `").removesuffix("`") for line in content.splitlines() if line.startswith("### `")
    ) == (
        "topology-decision-matrix",
        *CANONICAL_NODES,
    )


def test_gap_closure_freezes_the_authoritative_topology_decisions() -> None:
    content = GAP_CLOSURE_PATH.read_text(encoding="utf-8")

    for node_id, decision in TOPOLOGY_DECISIONS.items():
        row = next(line for line in content.splitlines() if line.startswith(f"| {node_id} |"))
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[8] == decision
        assert cells[8] in VALID_TOPOLOGY_DECISIONS

    assert "project_guideline_evidence_from_handoff != select_medication_guidelines" in content
    assert "post-generation Card validation != medication_guideline_safety_filter" in content
    assert "EVIDENCE_CONFLICTED != conflict_gate" in content
    assert "orchestrate_guide_generation_card != compose_personalized_guide" in content
    assert "| classification | `THIN_EXTRACTION` |" in content


def test_safety_filter_is_repositioned_and_has_only_the_typed_carrier_blocker() -> None:
    content = GAP_CLOSURE_PATH.read_text(encoding="utf-8")

    assert "after `compose_personalized_guide`" in content
    assert "PATIENT_CONTEXT_TYPED_CARRIER_MISSING" in content
    assert "patient_context_digest` is only a SHA-256 identity" in content
    assert "_is_valid_draft_shape()" in content


def test_freshness_retains_its_exact_runtime_evaluator_blocker() -> None:
    content = GAP_CLOSURE_PATH.read_text(encoding="utf-8")

    assert "FRESHNESS_RUNTIME_EVALUATOR_MISSING" in content


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
