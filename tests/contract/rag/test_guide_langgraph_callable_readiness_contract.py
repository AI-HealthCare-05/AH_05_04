from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

READINESS_PATH = Path("docs/contracts/targets/post-mvp-1/guide-langgraph-callable-readiness-v1.md")

CANONICAL_GUIDE_NODES = (
    "load_pinned_execution_context",
    "load_pinned_runtime_release_bundle",
    "load_verified_medication_identifications",
    "validate_bundle_and_source_freshness",
    "product_safety_overlay_gate_if_bundle_capability_enabled",
    "retrieve_medication_guidance",
    "select_medication_guidelines",
    "medication_guideline_safety_filter",
    "conflict_gate",
    "compose_personalized_guide",
    "claim_citation_validator",
    "release_gate",
    "persist_guide",
)

CALLABLE_STATUSES = {"EXACT_CALLABLE", "THIN_ADAPTER_NEEDED"}
VALID_STATUSES = CALLABLE_STATUSES | {
    "MISSING_SEMANTIC_CALLABLE",
    "EXTERNAL_SYNC_BACKEND_BOUNDARY",
}


@dataclass(frozen=True)
class ReadinessEntry:
    node_id: str
    actual_symbol: str
    status: str
    authority_owner: str
    required_next_action: str


def _new_entry(node_id: str) -> dict[str, str]:
    return {
        "node_id": node_id,
        "actual_symbol": "",
        "status": "",
        "authority_owner": "",
        "required_next_action": "",
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
