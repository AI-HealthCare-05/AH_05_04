from __future__ import annotations

import re
from pathlib import Path

BINDING_PATH = Path("docs/contracts/targets/post-mvp-1/guide-medication-guidance-retrieval-binding-v1.md")
READINESS_PATH = Path("docs/contracts/targets/post-mvp-1/guide-langgraph-callable-readiness-v1.md")

BLOCKER_STATES = {
    "GUIDE_RUNTIME_REQUEST_CARRIER_MISSING",
    "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING",
    "GUIDE_RETRIEVAL_REQUEST_AUTHORITY_LOOKUP_READY",
    "GUIDE_RETRIEVAL_OUTCOME_BINDING_READY",
    "GUIDE_RETRIEVAL_TERMINAL_REPLAY_PAYLOAD_UNAVAILABLE",
}

AVAILABLE_COMPONENTS = {
    "execute_hybrid_retrieve",
    "lookup_request_decision_refs",
    "compose_guide_authority_with_production_retrieval",
    "hydrate_guide_retrieval_content",
    "assemble_authoritative_guide_evidence_handoff",
}

CANONICAL_STATUS_BY_OTHER_NODE = {
    "load_pinned_execution_context": "EXTERNAL_SYNC_BACKEND_BOUNDARY",
    "load_pinned_runtime_release_bundle": "EXTERNAL_SYNC_BACKEND_BOUNDARY",
    "load_verified_medication_identifications": "EXTERNAL_SYNC_BACKEND_BOUNDARY",
    "validate_bundle_and_source_freshness": "MISSING_SEMANTIC_CALLABLE",
    "medication_guideline_safety_filter": "MISSING_SEMANTIC_CALLABLE",
    "compose_personalized_guide": "THIN_ADAPTER_NEEDED",
    "claim_citation_validator": "EXACT_CALLABLE",
    "release_gate": "EXACT_CALLABLE",
    "persist_guide": "EXTERNAL_SYNC_BACKEND_BOUNDARY",
}


def _readiness_entry(node_id: str) -> str:
    text = READINESS_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"^### `{re.escape(node_id)}`\n(?P<body>.*?)(?=^### `|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing readiness entry: {node_id}"
    return match.group("body")


def _entry_status(node_id: str) -> str:
    entry = _readiness_entry(node_id)
    match = re.search(r"^\| status \| `(?P<status>[A-Z_]+)` \|$", entry, flags=re.MULTILINE)
    assert match is not None, f"missing readiness status: {node_id}"
    return match.group("status")


def test_binding_freeze_documents_exact_blockers_and_existing_kernels() -> None:
    text = BINDING_PATH.read_text(encoding="utf-8")

    documented_codes = set(re.findall(r"^### B[1-5] — `([A-Z_]+)`$", text, flags=re.MULTILINE))
    assert documented_codes == BLOCKER_STATES
    assert all(component in text for component in AVAILABLE_COMPONENTS)
    assert "retrieval engine is missing" not in text.lower()
    assert "hybrid retrieval is not implemented" not in text.lower()


def test_binding_freeze_forbids_speculative_recovery_paths() -> None:
    text = BINDING_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "MUST NOT re-search on terminal replay",
        "MUST NOT treat `selected_hits=()` as a successful selection",
        "MUST NOT infer the latest or current Source/Member Decision",
        "MUST NOT accept caller-asserted raw runtime facts",
        "MUST NOT construct an unapproved `HybridRetrieveOutcome` → `ProductionRetrievalOutcome` projection",
        "#577 Worker handoff is not a prerequisite",
        "LangGraph implementation is out of scope.",
    )
    for anchor in required_anchors:
        assert anchor in text


def test_readiness_keeps_retrieval_missing_and_other_nodes_stable() -> None:
    retrieval_entry = _readiness_entry("retrieve_medication_guidance")

    assert _entry_status("retrieve_medication_guidance") == "MISSING_SEMANTIC_CALLABLE"
    assert "execute_hybrid_retrieve" in retrieval_entry
    assert "GUIDE_RUNTIME_REQUEST_CARRIER_MISSING" in retrieval_entry
    assert "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING" in retrieval_entry
    assert "GUIDE_RETRIEVAL_TERMINAL_REPLAY_PAYLOAD_UNAVAILABLE" in retrieval_entry
    for node_id, expected_status in CANONICAL_STATUS_BY_OTHER_NODE.items():
        assert _entry_status(node_id) == expected_status
