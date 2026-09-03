import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ai_worker.tasks.rag.source_governance import (
    SyntheticEnvironment,
    SyntheticGuardDecision,
    SyntheticGuardManifestEntry,
    SyntheticGuardOperation,
    SyntheticImmutableReference,
    SyntheticOriginGuardBinding,
    SyntheticSourceGovernanceFacts,
    SyntheticUsePurpose,
    evaluate_synthetic_source_governance,
)
from scripts.verify_rag_01_receipt import (
    calculate_receipt_hash,
    load_receipt,
    verify_receipt_hash,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "rag" / "source_contract_receipt.json"
LOCAL_TARGET_PATH = PROJECT_ROOT / "docs" / "contracts" / "targets" / "post-mvp-1" / "rag-source-ingestion-v1.md"
DECISION_PATH = PROJECT_ROOT / "docs" / "governance" / "decisions" / "2026-08-31-rag-p0-contract-freeze.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_facts(receipt: dict[str, Any], overrides: dict[str, Any]) -> SyntheticSourceGovernanceFacts:
    facts = receipt["synthetic_fact_template"] | overrides

    def references(field: str) -> tuple[SyntheticImmutableReference, ...]:
        return tuple(SyntheticImmutableReference(**reference) for reference in facts[field])

    def entries(field: str) -> tuple[SyntheticGuardManifestEntry, ...]:
        return tuple(
            SyntheticGuardManifestEntry(
                **{
                    **entry,
                    "purpose_code": SyntheticUsePurpose(entry["purpose_code"]),
                }
            )
            for entry in facts[field]
        )

    origin = facts["origin_request_guard"]
    if origin is not None:
        origin = SyntheticOriginGuardBinding(
            **{
                **origin,
                "decision": SyntheticGuardDecision(origin["decision"]),
                "operation": SyntheticGuardOperation(origin["operation"]),
                "environment": SyntheticEnvironment(origin["environment"]),
                "request_scope_codes": tuple(origin["request_scope_codes"]),
            }
        )

    return SyntheticSourceGovernanceFacts(
        **{
            **facts,
            "expected_purpose": SyntheticUsePurpose(facts["expected_purpose"]),
            "observed_purpose": SyntheticUsePurpose(facts["observed_purpose"]),
            "expected_environment": SyntheticEnvironment(facts["expected_environment"]),
            "observed_environment": SyntheticEnvironment(facts["observed_environment"]),
            "operation": SyntheticGuardOperation(facts["operation"]),
            "expected_immutable_references": references("expected_immutable_references"),
            "observed_immutable_references": references("observed_immutable_references"),
            "target_manifest_entries": entries("target_manifest_entries"),
            "selection_manifest_entries": entries("selection_manifest_entries"),
            "request_scope_codes": tuple(facts["request_scope_codes"]) if facts["request_scope_codes"] else None,
            "origin_request_guard": origin,
        }
    )


def test_source_governance_receipt_has_a_stable_canonical_hash() -> None:
    receipt = load_receipt(RECEIPT_PATH)

    assert verify_receipt_hash(RECEIPT_PATH) == receipt["receipt_hash"]["value"]

    regenerated = deepcopy(receipt)
    regenerated["generated_at"] = "2099-01-01T00:00:00+09:00"
    assert calculate_receipt_hash(regenerated) == receipt["receipt_hash"]["value"]


def test_source_governance_fixture_fails_closed_for_each_ineligible_case() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    cases = {case["case_id"]: case for case in receipt["synthetic_eligibility_cases"]}

    assert cases["approved-current-member"]["expected_guard_decision"] == "PASS"
    assert cases["approval-unapproved"]["expected_guard_decision"] == "FAIL"
    assert cases["approval-expired"]["expected_guard_decision"] == "FAIL"
    assert cases["snapshot-freshness-stale"]["expected_guard_decision"] == "FAIL"
    assert cases["revocation-unresolved"]["expected_guard_decision"] == "FAIL"
    assert cases["snapshot-incomplete"]["expected_guard_decision"] == "FAIL"
    assert cases["schema-drift"]["expected_guard_decision"] == "FAIL"
    assert cases["bundle-member-mismatch"]["expected_guard_decision"] == "FAIL"

    for case in cases.values():
        assert case["runtime_bundle_publication_allowed"] is False


def test_source_governance_fixture_executes_the_synthetic_guard_contract() -> None:
    receipt = load_receipt(RECEIPT_PATH)

    for case in receipt["synthetic_eligibility_cases"]:
        result = evaluate_synthetic_source_governance(_synthetic_facts(receipt, case["fact_overrides"]))

        assert result.decision.value == case["expected_guard_decision"]
        assert [reason.value.lower() for reason in result.observation_reasons] == case["expected_observation_codes"]


def test_source_governance_receipt_keeps_unimplemented_dependencies_and_publication_closed() -> None:
    receipt = load_receipt(RECEIPT_PATH)

    predecessor_gate = receipt["predecessor_receipt_gate"]
    assert predecessor_gate["status"] == "BLOCKED"
    assert predecessor_gate["blocking_code"] == "BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT"
    assert predecessor_gate["issues"] == [155, 165, 166]

    activation = receipt["runtime_activation"]
    assert activation["public_track_f"] is False
    assert activation["actual_source_activation_allowed"] is False


def test_source_governance_receipt_does_not_claim_unconnected_source_artifacts() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    binding = receipt["source_snapshot_approval_binding"]

    assert receipt["verification_status"] == "BLOCKED"
    assert receipt["execution_status"] == "NOT_IMPLEMENTED"
    assert receipt["synthetic_contract_execution"]["status"] == "COMPLETED"
    assert receipt["synthetic_contract_execution"]["decision"] == "PASS"
    assert receipt["source_readiness"]["status"] == "BLOCKED"
    assert receipt["decision_status"] is None
    assert binding["status"] == "NOT_CONNECTED"
    assert all(
        binding[field] is None
        for field in (
            "source_code",
            "endpoint_code",
            "operation_code",
            "source_version",
            "source_content_sha256",
            "source_lifecycle_status",
            "endpoint_lifecycle_status",
            "endpoint_runtime_status",
            "endpoint_acquisition_status",
            "operation_runtime_status",
            "operation_acquisition_status",
            "snapshot_version",
            "snapshot_canonical_checksum",
            "snapshot_schema_version",
            "snapshot_parser_version",
            "snapshot_normalization_version",
            "snapshot_verification_status",
            "snapshot_verification_version",
            "snapshot_verification_hash",
            "license_status",
            "reuse_terms",
            "attribution_text",
            "clinical_status",
            "allowed_environments",
            "allowed_scope",
            "approval_status",
            "approval_purpose",
            "approval_version",
            "approval_hash",
            "approval_effective_at",
            "approval_valid_until",
            "freshness_policy_version",
            "freshness_policy_hash",
            "freshness_verified_at",
        )
    )
    assert receipt["revocation_contract"] == {
        "new_use_allowed": False,
        "historical_provenance_preserved": True,
        "reusable_as_current": False,
    }


def test_machine_receipt_captures_operation_specific_guard_boundaries() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    boundary = receipt["eligibility_guard_boundary"]
    legacy_combined_manifest_fields = {
        "expected_target_count",
        "observed_target_count",
        "expected_target_hash",
        "observed_target_hash",
        "expected_selection_count",
        "observed_selection_count",
        "expected_selection_hash",
        "observed_selection_hash",
        "selection_is_subset_of_target",
    }

    assert legacy_combined_manifest_fields.isdisjoint(receipt["synthetic_fact_template"])
    assert "request_and_citation_require_active_environment" in boundary["required_checks"]
    assert (
        "request_and_citation_require_request_active_target_bundle_id_and_manifest_exact_match"
        in boundary["required_checks"]
    )
    assert boundary["selection_rules"] == {
        "modeled_required_for": ["REQUEST", "CITATION_AUTHORIZATION"],
        "documented_not_executed": [
            "EVALUATION_CANDIDATE",
            "EVALUATION_REQUEST",
            "PLANNED_ACTIVATION",
            "EMERGENCY_ROLLBACK",
            "RESUME",
        ],
    }
    assert boundary["retrieval_approval_authorizes_patient_citation"] is False
    assert boundary["citation_origin_request_binding"] == {
        "origin_decision": "PASS",
        "operation": "REQUEST",
        "exact_match": [
            "bundle_id",
            "environment",
            "bundle_manifest_hash",
            "request_scope_codes",
            "scope_manifest_hash",
        ],
        "scope_codes_canonicalization": "nonempty-unique-NFC-UTF8-byte-sorted-compact-JSON-array-sha256",
    }
    assert boundary["target_and_selection_manifest_validation"]["caller_asserted_subset_is_sufficient"] is False
    assert boundary["target_and_selection_manifest_validation"]["envelope_keys"] == [
        "guard_target_manifest_spec_version",
        "target_bundle_manifest_hash",
        "set_role",
        "entry_kind",
        "entries",
    ]
    assert boundary["target_and_selection_manifest_validation"]["set_roles"] == [
        "ELIGIBILITY_TARGET",
        "OPERATION_SELECTION",
    ]
    assert boundary["target_and_selection_manifest_validation"]["entry_kinds"] == [
        "RELEASE_SOURCE",
        "SNAPSHOT_MEMBER",
    ]
    assert boundary["target_and_selection_manifest_validation"]["minimum_count_per_required_entry_kind"] == 1
    assert boundary["target_and_selection_manifest_validation"]["separate_count_and_hash_per_entry_kind"] is True
    assert boundary["target_and_selection_manifest_validation"]["duplicate_entries_rejected"] is True
    assert boundary["target_and_selection_manifest_validation"]["member_null_rules"] == {
        "RELEASE_SOURCE": "endpoint_code, operation_code, artifact_code, artifact_version are null",
        "SNAPSHOT_MEMBER": ("exactly one of endpoint_code+operation_code or artifact_code+artifact_version is present"),
    }
    assert boundary["target_and_selection_manifest_validation"]["release_source_entry_fields"] == [
        "source_code",
        "source_version",
        "purpose_code",
        "approval_version",
        "scope_policy_hash",
        "freshness_policy_hash",
        "bundle_build_source_verification_stable_key",
        "source_manifest_member_hash",
    ]
    assert boundary["rollback_without_eligible_candidate_results_in"] == "SUSPENDED"


def test_contract_authority_is_complete_and_local_artifacts_match_bytes() -> None:
    receipt = load_receipt(RECEIPT_PATH)
    authority = receipt["contract_authority"]

    assert authority["authority_manifest"] == {
        "document_set_id": "post-mvp-rag-evaluation-contract",
        "version": "2026-08-29.11",
        "sha256": "f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570",
    }
    assert authority["source_policy"]["version"] == "1.18"
    assert authority["source_policy"]["sha256"] == "35842d2cbe54201ff9fb5580616055eda613fe4c16ac6d60daa7f8859d2f28e3"
    assert authority["database_target"]["version"] == "1.47"
    assert authority["database_target"]["sha256"] == "f88ec11aaa6671184f2d0f5076219bf2ad51525b9e6a136ec5389afd2af82aea"
    assert authority["local_target"]["version"] == "1"
    assert authority["local_target"]["sha256"] == _sha256(LOCAL_TARGET_PATH)
    assert authority["decision"]["decision_id"] == "PD-125-20260831"
    assert authority["decision"]["sha256"] == _sha256(DECISION_PATH)

    execution = receipt["synthetic_contract_execution"]
    for artifact in execution["suite_artifacts"]:
        assert artifact["sha256"] == _sha256(PROJECT_ROOT / artifact["path"])
    canonical_input = json.dumps(execution["input_manifest"], sort_keys=True, separators=(",", ":")).encode()
    assert execution["input_manifest_hash"] == hashlib.sha256(canonical_input).hexdigest()


def test_receipt_freezes_source_purpose_matrix_successors_and_publication_gates() -> None:
    receipt = load_receipt(RECEIPT_PATH)

    purposes = {row["purpose"]: row for row in receipt["source_purpose_eligibility_matrix"]}
    assert set(purposes) == {
        "PRODUCT_IDENTIFICATION",
        "SAFETY_ROUTING",
        "RULE_DERIVATION",
        "RETRIEVAL",
        "PATIENT_CITATION",
    }
    assert purposes["PATIENT_CITATION"]["separate_approval_required"] is True
    assert purposes["PATIENT_CITATION"]["retrieval_approval_is_sufficient"] is False
    assert receipt["downstream_prerequisites"] == {
        "167": "BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT",
        "168": "BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT",
        "170": "BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT",
        "175": "BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT",
    }
    assert receipt["runtime_activation"]["required_track_f_gates"] == [
        "EXT-MED-002",
        "EXT-PHARM-001",
        "EXT-SOURCE-001",
        "EXT-SOURCE-002",
        "EXT-PRIV-001",
        "EXT-PRIV-002",
        "EXT-SAFETY-001",
    ]
