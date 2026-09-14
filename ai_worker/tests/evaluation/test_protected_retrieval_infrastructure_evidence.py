"""Issue #368 repository implementation evidence tests."""

import json
from pathlib import Path
from typing import cast

import pytest

from ai_worker.tasks.evaluation.canonical import JsonValue
from ai_worker.tasks.evaluation.protected_retrieval_infrastructure_evidence import (
    EVIDENCE_JSON_PATH,
    EVIDENCE_MARKDOWN_PATH,
    build_protected_retrieval_infrastructure_evidence,
    render_protected_retrieval_infrastructure_evidence,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_infrastructure_evidence_separates_implementation_from_activation() -> None:
    evidence = build_protected_retrieval_infrastructure_evidence(REPOSITORY_ROOT)

    assert evidence["repository_adapter_status"] == "PARTIALLY_IMPLEMENTED"
    assert evidence["authorization_control_c1_status"] == "IMPLEMENTED_IN_REPOSITORY"
    assert evidence["approval_ingestion_status"] == "IMPLEMENTED_IN_REPOSITORY"
    assert evidence["grant_revoke_expire_status"] == "IMPLEMENTED_IN_REPOSITORY"
    assert evidence["production_approval_source_connector_status"] == "NOT_IMPLEMENTED"
    assert evidence["dataset_lifecycle_freeze_status"] == "NOT_IMPLEMENTED"
    assert evidence["identity_administration_status"] == "NOT_IMPLEMENTED"
    assert evidence["remaining_repository_scope"] == [
        "PRODUCTION_APPROVAL_SOURCE_CONNECTOR",
        "DATASET_TRANSITION_AND_FREEZE_SERVICE",
        "IDENTITY_REGISTRATION_DISABLE_SERVICE",
    ]
    assert evidence["effective_enforcement_status"] == "NOT_IMPLEMENTED"
    assert evidence["access_authorized"] is False
    assert evidence["holdout_authored"] is False
    assert evidence["freeze_recorded"] is False
    assert evidence["actual_run_ref"] is None
    assert evidence["disposal_status"] == "BLOCKED_BY_ISSUE_425"
    assert evidence["release_eligible"] is False
    implementation_files = cast(list[dict[str, JsonValue]], evidence["implementation_files"])
    assert [item["component"] for item in implementation_files] == [
        "ASYNC_KERNEL_SEAM",
        "POSTGRESQL_ADAPTER",
        "FAIL_CLOSED_CONFIG",
        "EXPLICIT_RUNTIME_ASSEMBLY",
        "PROTECTED_ROLE_POLICY",
        "ISOLATED_MIGRATION_ENV",
        "ISOLATED_MIGRATION",
        "AUTHORIZATION_CONTROL_CONTRACT",
        "POSTGRESQL_CONTROL_ADAPTER",
        "AUTHORIZATION_CONTROL_MIGRATION",
    ]
    verification = cast(list[dict[str, JsonValue]], evidence["verification"])
    assert verification[-1] == {
        "command_id": "WORKER_IMAGE_PROTECTED_OFF_IMPORT",
        "result": "PASSED",
    }


def test_infrastructure_evidence_contains_only_non_sensitive_scalars() -> None:
    evidence = build_protected_retrieval_infrastructure_evidence(REPOSITORY_ROOT)
    forbidden = (
        "credential",
        "authorization receipt body",
        "hmac value",
        "key material",
        "holdout question",
        "gold body",
        "hard-negative label",
        "database host",
        "database login",
        "deployed schema",
    )

    def inspect(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                assert not any(fragment in str(key).casefold() for fragment in forbidden)
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)
        elif isinstance(value, str):
            assert not any(fragment in value.casefold() for fragment in forbidden)

    inspect(evidence)


def test_infrastructure_evidence_rejects_unallowlisted_recursive_fields() -> None:
    evidence = build_protected_retrieval_infrastructure_evidence(REPOSITORY_ROOT)
    decision = cast(dict[str, JsonValue], evidence["decision"])
    decision["id"] = {"unexpected": "must-not-appear"}

    with pytest.raises(RuntimeError, match="allowlisted"):
        render_protected_retrieval_infrastructure_evidence(evidence)


def test_committed_infrastructure_evidence_matches_builder() -> None:
    evidence = build_protected_retrieval_infrastructure_evidence(REPOSITORY_ROOT)

    assert json.loads((REPOSITORY_ROOT / EVIDENCE_JSON_PATH).read_text()) == evidence
    assert (REPOSITORY_ROOT / EVIDENCE_MARKDOWN_PATH).read_text() == (
        render_protected_retrieval_infrastructure_evidence(evidence)
    )
