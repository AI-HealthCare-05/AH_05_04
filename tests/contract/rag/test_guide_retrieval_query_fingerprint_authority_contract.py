from pathlib import Path

AUTHORITY_PATH = Path("docs/contracts/targets/post-mvp-1/guide-retrieval-query-fingerprint-authority-v1.md")
QUERY_AUTHORITY_PATH = Path("docs/contracts/targets/post-mvp-1/guide-medication-retrieval-query-authority-v1.md")
BINDING_PATH = Path("docs/contracts/targets/post-mvp-1/guide-medication-guidance-retrieval-binding-v1.md")


def test_fingerprint_authority_contract_freezes_the_narrow_production_gap() -> None:
    text = AUTHORITY_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "#180 B2-2",
        "PD-315-20260908",
        "Approved (2026-09-17)",
        "versioned HMAC preimage",
        "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING",
        "exact production algorithm identifier",
        "key_version naming authority",
        "secret/key owner or storage authority",
        "approved Worker runtime key-injection dependency",
        "QueryBindingVerifierPort",
        "verifier artifact identity",
        "HMAC key hardcode",
        "fallback key",
        "raw query",
        "B1",
        "B3",
        "B5",
        "Worker/#577",
    )
    for anchor in required_anchors:
        assert anchor in text


def test_fingerprint_authority_contract_does_not_promote_synthetic_or_unrelated_key_policy() -> None:
    text = AUTHORITY_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "MUST NOT promote `sha256` / `v1` synthetic fixtures",
        "MUST NOT reuse the Backend idempotency HMAC key",
        "caller MUST NOT select algorithm, key_version, secret, or hash function",
        "No production Python implementation is added",
        "THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS",
        "MISSING_SEMANTIC_CALLABLE",
    )
    for anchor in required_anchors:
        assert anchor in text


def test_b2_documents_the_exact_blocker_without_changing_other_binding_states() -> None:
    query_text = QUERY_AUTHORITY_PATH.read_text(encoding="utf-8")
    binding_text = BINDING_PATH.read_text(encoding="utf-8")

    assert "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING" in query_text
    assert "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_BLOCKED_BY_ALGORITHM_AUTHORITY_MISSING" in binding_text
    assert "GUIDE_RETRIEVAL_OUTCOME_BINDING_READY" in binding_text
    assert "GUIDE_RETRIEVAL_REQUEST_AUTHORITY_LOOKUP_READY" in binding_text
    assert "GUIDE_RETRIEVAL_TERMINAL_REPLAY_PAYLOAD_READY" in binding_text
