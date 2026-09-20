from pathlib import Path

CONTRACT_PATH = Path("docs/contracts/targets/post-mvp-1/guide-retrieval-outcome-binding-v1.md")


def test_outcome_binding_contract_freezes_the_narrow_697_projection() -> None:
    text = CONTRACT_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "HybridRetrieveOutcome",
        "ProductionRetrievalOutcome",
        "`HybridRetrieveOutcome.status` → `ProductionRetrievalOutcome.status`",
        "`HybridRetrieveOutcome.search_receipt` → `ProductionRetrievalOutcome.receipt`",
        "`HybridRetrieveOutcome.gate_outcome` → `ProductionRetrievalOutcome.gate_outcome`",
        "`ProductionRetrievalOutcome.search_success` → `None`",
        "GUIDE_RETRIEVAL_OUTCOME_BINDING_READY",
        "compose_guide_authority_with_production_retrieval",
    )
    for anchor in required_anchors:
        assert anchor in text


def test_outcome_binding_contract_forbids_recovery_or_new_retrieval_meaning() -> None:
    text = CONTRACT_PATH.read_text(encoding="utf-8")

    required_anchors = (
        "SEARCH_RECEIPT_REQUIRED",
        "MUST NOT fabricate `search_success`",
        "MUST NOT recompute a receipt",
        "MUST NOT re-search",
        "MUST NOT rank, filter, or alter selected hits",
        "B5 owns historical payload recovery",
        "#697 remains unchanged",
        "non-empty selected_hits",
    )
    for anchor in required_anchors:
        assert anchor in text
