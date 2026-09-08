from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.natural_language_retrieval_protected_runner_foundation import (
    PROTECTED_RUNNER_FOUNDATION_JSON_PATH,
    PROTECTED_RUNNER_FOUNDATION_MARKDOWN_PATH,
    build_protected_runner_foundation,
    render_protected_runner_foundation_markdown,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
FOUNDATION_JSON_PATH = REPOSITORY_ROOT / PROTECTED_RUNNER_FOUNDATION_JSON_PATH
FOUNDATION_MARKDOWN_PATH = REPOSITORY_ROOT / PROTECTED_RUNNER_FOUNDATION_MARKDOWN_PATH
PREPARATION_PATH = REPOSITORY_ROOT / "docs/validation/rag/issue-273/holdout-freeze-preparation.json"
PREPARATION_RAW_SHA256 = "40ea344c378298d99c14c372c27296322854d8e9b055fa179592568ca88bc192"
PREPARATION_SELF_SHA256 = "b4a0a113d9efce867a434875f18ee259431d226a9cf1e4dcaed28152920600b6"


def test_foundation_records_issue_source_and_only_the_implemented_policy_boundary() -> None:
    packet = cast(dict[str, Any], build_protected_runner_foundation(REPOSITORY_ROOT))

    assert packet["phase"] == "PHASE_B3_PROTECTED_RUNNER_FOUNDATION"
    assert packet["issue"] == {
        "api_resource_id": "I_kwDOT3EWNs8AAAABQTH1bg",
        "created_at": "2026-09-08T15:35:55.000000Z",
        "number": 368,
        "source_repository": "AI-HealthCare-05/AH_05_04",
        "state_at_capture": "OPEN",
        "url": "https://github.com/AI-HealthCare-05/AH_05_04/issues/368",
    }
    assert packet["issue_canonical_subset_sha256"] == canonical_sha256(packet["issue"])
    assert packet["policy_foundation_status"] == "IMPLEMENTED"
    assert packet["issue_completion_status"] == "IN_PROGRESS"
    assert packet["effective_enforcement_status"] == "NOT_IMPLEMENTED"
    assert packet["infrastructure_adapter_status"] == "NOT_IMPLEMENTED"
    assert packet["reconciliation_adapter_status"] == "NOT_IMPLEMENTED"
    assert packet["access_authorized"] is False
    assert packet["holdout_authored"] is False
    assert packet["freeze_recorded"] is False
    assert packet["actual_run_ref"] is None
    assert packet["release_eligible"] is False
    assert packet["foundation_sha256"] == canonical_sha256(
        packet, excluded_top_level_keys=frozenset({"foundation_sha256"})
    )


def test_foundation_keeps_the_preparation_snapshot_immutable() -> None:
    packet = cast(dict[str, Any], build_protected_runner_foundation(REPOSITORY_ROOT))

    assert sha256(PREPARATION_PATH.read_bytes()).hexdigest() == PREPARATION_RAW_SHA256
    assert packet["holdout_preparation_ref"] == {
        "id": "issue-273-holdout-freeze-preparation",
        "raw_sha256": PREPARATION_RAW_SHA256,
        "self_sha256": PREPARATION_SELF_SHA256,
        "version": "1.0.0",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda packet: packet["issue"].update({"state_at_capture": "CLOSED"}),
        lambda packet: packet["issue"].update({"api_resource_id": "forged"}),
        lambda packet: packet.update({"issue_canonical_subset_sha256": "d" * 64}),
        lambda packet: packet.update({"access_authorized": True}),
        lambda packet: packet.update({"freeze_recorded": True}),
        lambda packet: packet.update({"effective_enforcement_status": "IMPLEMENTED"}),
        lambda packet: packet.update({"issue_completion_status": "COMPLETED"}),
        lambda packet: packet.update({"release_eligible": True}),
    ],
)
def test_foundation_rejects_rehashed_source_or_premature_state(mutation: Any) -> None:
    packet = cast(dict[str, Any], build_protected_runner_foundation(REPOSITORY_ROOT))
    mutation(packet)
    packet["foundation_sha256"] = canonical_sha256(packet, excluded_top_level_keys=frozenset({"foundation_sha256"}))

    with pytest.raises(RuntimeError):
        render_protected_runner_foundation_markdown(packet)


@pytest.mark.parametrize(
    "forbidden_key", ["query", "gold_body", "credential", "protected_path", "hmac_value", "fingerprint_value"]
)
def test_foundation_rejects_protected_public_fields(forbidden_key: str) -> None:
    packet = cast(dict[str, Any], build_protected_runner_foundation(REPOSITORY_ROOT))
    packet["unexpected"] = {forbidden_key: "must-not-be-public"}

    with pytest.raises(RuntimeError, match="protected HOLDOUT field"):
        render_protected_runner_foundation_markdown(packet)


def test_committed_foundation_artifacts_equal_a_fresh_build() -> None:
    packet = build_protected_runner_foundation(REPOSITORY_ROOT)

    assert FOUNDATION_JSON_PATH.read_bytes() == canonical_json_bytes(packet)
    assert FOUNDATION_MARKDOWN_PATH.read_text(encoding="utf-8") == render_protected_runner_foundation_markdown(packet)


def test_foundation_artifacts_do_not_contain_holdout_content_or_storage_locations() -> None:
    combined = FOUNDATION_JSON_PATH.read_bytes() + FOUNDATION_MARKDOWN_PATH.read_bytes()
    lowered = combined.lower()

    for marker in (
        b'"query"',
        b'"gold_body"',
        b'"credential"',
        b'"protected_path"',
        b'"hmac_value"',
        b'"fingerprint_value"',
        b"postgresql://",
        b"s3://",
    ):
        assert marker not in lowered

    json.loads(FOUNDATION_JSON_PATH.read_bytes())
