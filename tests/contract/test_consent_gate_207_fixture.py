from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "consent" / "consent_gate_207_cases.json"

EXPECTED_PURPOSES = {"OCR", "GUIDE", "CHAT", "NOTIFICATION"}
EXPECTED_STATUSES = {"GRANTED", "WITHDRAWN"}
EXPECTED_CASES = {
    "missing_row_blocks",
    "granted_current_policy_allows",
    "withdrawn_blocks",
    "lookup_error_fail_closed",
    "purpose_mismatch_blocks",
    "inactive_account_blocks",
    "owner_mismatch_blocks",
    "policy_version_mismatch_blocks",
}


def test_consent_gate_207_fixture_covers_shared_backend_worker_decision_table() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == "consent-gate-207.fixture@1"
    assert Path(fixture["decision"]).as_posix() == "docs/governance/decisions/2026-09-10-consent-gate-207.md"
    assert set(fixture["purposes"]) == EXPECTED_PURPOSES
    assert set(fixture["statuses"]) == EXPECTED_STATUSES
    assert set(fixture["current_policy_versions"]) == EXPECTED_PURPOSES

    cases = fixture["cases"]
    assert {case["case_id"] for case in cases} == EXPECTED_CASES

    allowed_cases = [case for case in cases if case["expected"]["allowed"]]
    assert [case["case_id"] for case in allowed_cases] == ["granted_current_policy_allows"]

    for case in cases:
        assert case["purpose"] in EXPECTED_PURPOSES
        assert case["account_status"] in {"ACTIVE", "WITHDRAWAL_REQUESTED", "WITHDRAWN"}
        if case["row"] is not None:
            assert case["row"]["purpose"] in EXPECTED_PURPOSES
            assert case["row"]["status"] in EXPECTED_STATUSES
            assert case["row"]["policy_version"]
        if not case["expected"]["allowed"]:
            assert case["expected"]["provider_calls"] == 0
            assert case["expected"]["reason"]
