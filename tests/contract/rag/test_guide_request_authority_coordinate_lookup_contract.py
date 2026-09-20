from pathlib import Path

CONTRACT_PATH = Path("docs/contracts/targets/post-mvp-1/guide-request-authority-coordinate-lookup-v1.md")


def test_contract_freezes_the_complete_exact_historical_coordinate() -> None:
    text = CONTRACT_PATH.read_text(encoding="utf-8")

    required = (
        "GUIDE_RETRIEVAL_REQUEST_AUTHORITY_LOOKUP_READY",
        "request_guard_ref",
        "user_id",
        "request_operation_code",
        "decision_stage=REQUEST",
        "source_snapshot_id",
        "source_snapshot_member_id",
        "source_code",
        "source_version",
        "expected_source_decision_outcome",
        "expected_member_decision_outcome",
        "SourceMemberIdentity",
        "request_source_decision_ref",
        "request_member_decision_ref",
    )
    assert all(anchor in text for anchor in required)


def test_contract_forbids_latest_derivation_and_partial_results() -> None:
    text = CONTRACT_PATH.read_text(encoding="utf-8")

    required = (
        "REPEATABLE READ, READ ONLY",
        "no `CURRENT`",
        "no partial result",
        "not derived from the caller's coordinate",
        "does not require a migration",
        "does not implement B1 carrier completion",
        "B5 terminal replay readback",
    )
    assert all(anchor in text for anchor in required)
