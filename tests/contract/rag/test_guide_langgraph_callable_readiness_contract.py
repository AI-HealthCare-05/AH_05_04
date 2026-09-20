from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

READINESS_PATH = Path("docs/contracts/targets/post-mvp-1/guide-langgraph-callable-readiness-v1.md")
RUNTIME_PATH = Path("docs/contracts/targets/post-mvp-1/rag-runtime-v1.md")

CANONICAL_GUIDE_NODES = (
    "load_pinned_execution_context",
    "load_pinned_runtime_release_bundle",
    "load_verified_medication_identifications",
    "validate_bundle_and_source_freshness",
    "retrieve_medication_guidance",
    "compose_personalized_guide",
    "medication_guideline_safety_filter",
    "claim_citation_validator",
    "release_gate",
    "persist_guide",
)

CALLABLE_STATUSES = {"EXACT_CALLABLE", "THIN_ADAPTER_NEEDED"}
VALID_STATUSES = CALLABLE_STATUSES | {
    "MISSING_SEMANTIC_CALLABLE",
    "EXTERNAL_SYNC_BACKEND_BOUNDARY",
}
ALIGNMENT_DECISIONS = {
    "RETAIN_STANDALONE",
    "MERGE_INTO_EXISTING_BOUNDARY",
    "RETIRE_FROM_CURRENT_MVP_TOPOLOGY",
    "RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION",
}


@dataclass(frozen=True)
class ReadinessEntry:
    node_id: str
    actual_symbol: str
    status: str
    authority_owner: str
    required_next_action: str
    alignment_decision: str = ""


def _new_entry(node_id: str) -> dict[str, str]:
    return {
        "node_id": node_id,
        "actual_symbol": "",
        "status": "",
        "authority_owner": "",
        "required_next_action": "",
        "alignment_decision": "",
    }


def _node_id(line: str) -> str | None:
    if line.startswith("### `") and line.endswith("`"):
        return line.removeprefix("### `").removesuffix("`")
    return None


def _record_field(entry: dict[str, str], line: str) -> None:
    if not line.startswith("|"):
        return
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    if len(cells) != 2:
        return
    field, value = cells
    field_name = {
        "actual_symbol": "actual_symbol",
        "status": "status",
        "authority_owner": "authority_owner",
        "required_next_action": "required_next_action",
        "alignment_decision": "alignment_decision",
    }.get(field)
    if field_name is not None:
        entry[field_name] = value.strip("`")


def _entries() -> list[ReadinessEntry]:
    entries: list[ReadinessEntry] = []
    current: dict[str, str] | None = None

    for line in READINESS_PATH.read_text(encoding="utf-8").splitlines():
        node_id = _node_id(line)
        if node_id is not None:
            if current is not None:
                entries.append(ReadinessEntry(**current))
            current = _new_entry(node_id)
            continue
        if current is not None:
            _record_field(current, line)

    if current is not None:
        entries.append(ReadinessEntry(**current))
    return entries


def _resolve(symbol: str) -> object:
    module_name, separator, attribute_name = symbol.partition(":")
    assert separator and module_name and attribute_name
    return getattr(importlib.import_module(module_name), attribute_name)


def test_readiness_matrix_matches_the_canonical_guide_node_set() -> None:
    entries = _entries()

    assert tuple(entry.node_id for entry in entries) == CANONICAL_GUIDE_NODES


def test_readiness_matrix_only_marks_importable_callables_ready() -> None:
    entries = _entries()

    for entry in entries:
        assert entry.status in VALID_STATUSES
        assert entry.actual_symbol
        assert entry.authority_owner
        assert entry.required_next_action
        if entry.status in CALLABLE_STATUSES:
            assert callable(_resolve(entry.actual_symbol))


def test_persist_guide_remains_an_explicit_sync_backend_boundary() -> None:
    entries = {entry.node_id: entry for entry in _entries()}

    persist_guide = entries["persist_guide"]
    assert persist_guide.status == "EXTERNAL_SYNC_BACKEND_BOUNDARY"
    assert "Backend" in persist_guide.authority_owner


def test_topology_decisions_remove_or_merge_nodes_without_fake_wrappers() -> None:
    entries = {entry.node_id: entry for entry in _entries()}

    assert entries["validate_bundle_and_source_freshness"].alignment_decision == "RETAIN_STANDALONE"
    assert (
        entries["medication_guideline_safety_filter"].alignment_decision
        == "RETAIN_STANDALONE_BUT_REPOSITION_AFTER_COMPOSITION"
    )
    assert entries["medication_guideline_safety_filter"].alignment_decision in ALIGNMENT_DECISIONS

    for removed_node in (
        "product_safety_overlay_gate_if_bundle_capability_enabled",
        "select_medication_guidelines",
        "conflict_gate",
    ):
        assert removed_node not in entries

    content = READINESS_PATH.read_text(encoding="utf-8")
    assert (
        "| `product_safety_overlay_gate_if_bundle_capability_enabled` | `RETIRE_FROM_CURRENT_MVP_TOPOLOGY` |" in content
    )
    assert "| `select_medication_guidelines` | `MERGE_INTO_EXISTING_BOUNDARY` |" in content
    assert "| `conflict_gate` | `MERGE_INTO_EXISTING_BOUNDARY` |" in content
    assert "StateGraph`, `langgraph` dependency" in content
    assert "THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS" in content
    assert "no node or pass-through wrapper is retained" in content


def test_guide_topology_repositions_safety_and_omits_retired_or_merged_nodes() -> None:
    guide_graph = RUNTIME_PATH.read_text(encoding="utf-8").split("### Guide Graph", 1)[1].split("### Rule-first", 1)[0]

    assert (
        """START
→ load_pinned_execution_context
→ load_pinned_runtime_release_bundle
→ load_verified_medication_identifications
→ validate_bundle_and_source_freshness
→ retrieve_medication_guidance
→ compose_personalized_guide
→ medication_guideline_safety_filter
→ claim_citation_validator
→ release_gate
→ persist_guide"""
        in guide_graph
    )
    for removed_node in (
        "product_safety_overlay_gate_if_bundle_capability_enabled",
        "select_medication_guidelines",
        "conflict_gate",
    ):
        assert f"→ {removed_node}" not in guide_graph


def test_remaining_callable_blockers_record_the_exact_non_mappings() -> None:
    entries = {entry.node_id: entry for entry in _entries()}
    content = READINESS_PATH.read_text(encoding="utf-8")

    for node_id in (
        "validate_bundle_and_source_freshness",
        "medication_guideline_safety_filter",
    ):
        assert entries[node_id].status == "MISSING_SEMANTIC_CALLABLE"

    assert "`freshness_eligible` and `scope_allowed`" in content
    assert "`freshness_policy_hash` or `scope_policy_hash`" in content
    assert "FRESHNESS_RUNTIME_EVALUATOR_MISSING" in content
    assert "PATIENT_CONTEXT_TYPED_CARRIER_MISSING" in content
    assert "patient_context_digest` is only a SHA-256 identity" in content
    assert "after `compose_personalized_guide`" in content
