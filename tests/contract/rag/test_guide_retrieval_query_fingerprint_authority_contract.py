from pathlib import Path

AUTHORITY_PATH = Path("docs/contracts/targets/post-mvp-1/guide-retrieval-query-fingerprint-authority-v1.md")
QUERY_AUTHORITY_PATH = Path("docs/contracts/targets/post-mvp-1/guide-medication-retrieval-query-authority-v1.md")
BINDING_PATH = Path("docs/contracts/targets/post-mvp-1/guide-medication-guidance-retrieval-binding-v1.md")


def test_fingerprint_authority_contract_freezes_the_production_implementation_boundary() -> None:
    text = AUTHORITY_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "#180 B2",
        "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY",
        "HMAC-SHA-256",
        "query-hmac@1",
        "guide-query-hmac-key@<positive-integer>",
        "backend/app/core/config.py",
        "backend/app/dependencies/services.py",
        "GuideQueryHmacKeyDependency",
        "QueryBindingVerifierPort",
        "guide-query-binding-verifier@1.0.0",
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


def test_fingerprint_authority_contract_keeps_secrets_and_unrelated_key_policy_out_of_scope() -> None:
    text = AUTHORITY_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "MUST NOT reuse the Backend idempotency HMAC key",
        "caller MUST NOT select algorithm, key_version, secret, or hash function",
        "THIN_LANGGRAPH_BLOCKED_BY_CANONICAL_CALLABLE_GAPS",
        "Chat orchestration",
    )
    for anchor in required_anchors:
        assert anchor in text


def test_b2_documents_ready_without_changing_other_binding_states() -> None:
    query_text = QUERY_AUTHORITY_PATH.read_text(encoding="utf-8")
    binding_text = BINDING_PATH.read_text(encoding="utf-8")

    assert "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY" in query_text
    assert "GUIDE_RETRIEVAL_QUERY_FINGERPRINT_AUTHORITY_READY" in binding_text
    assert "GUIDE_RETRIEVAL_OUTCOME_BINDING_READY" in binding_text
    assert "GUIDE_RETRIEVAL_REQUEST_AUTHORITY_LOOKUP_READY" in binding_text
    assert "GUIDE_RETRIEVAL_TERMINAL_REPLAY_PAYLOAD_READY" in binding_text
