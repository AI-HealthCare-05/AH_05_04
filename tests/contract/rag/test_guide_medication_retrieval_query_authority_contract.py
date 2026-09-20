from pathlib import Path

CONTRACT_PATH = Path("docs/contracts/targets/post-mvp-1/guide-medication-retrieval-query-authority-v1.md")


def test_query_authority_contract_freezes_only_pinned_snapshot_text_projection() -> None:
    text = CONTRACT_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "medication_name_snapshot",
        "strength_text_snapshot",
        "one verified medication → one canonical query unit",
        "guide-medication-retrieval-query-v1",
        "ASCII SPACE",
        "SensitiveText normalized_query",
        "MUST NOT use `canonical_code` as query text",
        "MUST NOT read the current product catalog",
        "MUST NOT concatenate multiple medications",
        "MUST NOT append an intent suffix",
        "MUST NOT silently normalize",
    )
    for anchor in required_anchors:
        assert anchor in text


def test_query_authority_contract_keeps_fingerprint_and_sync_carrier_fail_closed() -> None:
    text = CONTRACT_PATH.read_text(encoding="utf-8")

    assert "GUIDE_RETRIEVAL_QUERY_TEXT_AUTHORITY_READY_BUT_FINGERPRINT_BLOCKED" in text
    assert "QueryBindingVerifierPort" in text
    assert "No production implementation is present" in text
    assert "B1 Sync carrier" in text
    assert "MUST NOT promote `sha256` / `v1` synthetic fixtures" in text
    assert "Worker/#577" in text
