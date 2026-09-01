from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.loaders_contract import load_dataset
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.authoring_contract import (
    DatasetManifest,
    EvaluationContext,
    EvidenceMappingManifest,
    RetrievalExpected,
)
from ai_worker.tasks.evaluation.schemas.policy_contract import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
EVALS_ROOT = REPOSITORY_ROOT / "evals"
FOUNDATION_MANIFEST = EVALS_ROOT / "retrieval/manifests/dev-foundation-v1.dataset.json"
PROTECTED_RECEIPT = EVALS_ROOT / "provenance/dev-foundation-v1.protected-artifact-receipt.json"


def test_authoring_schema_uses_exact_section_17_fields() -> None:
    case_properties = DatasetManifest.model_json_schema()["properties"]
    assert set(case_properties) == {
        "schema_id",
        "schema_version",
        "dataset_code",
        "dataset_version",
        "scope",
        "description",
        "data_classification",
        "deidentification_approval_receipt_ref",
        "critical_claim_rubric_ref",
        "evidence_mapping_manifest_sha256",
        "evaluation_corpus_snapshot_ref",
        "case_resources",
        "partition_counts",
        "resource_set_hash",
        "fixture_git_commit_sha",
        "protected_artifact_receipt_ref",
        "status",
        "frozen_at",
        "review_provenance",
        "manifest_sha256",
    }
    assert set(EvidenceMappingManifest.model_json_schema()["properties"]) == {
        "schema_id",
        "schema_version",
        "mapping_id",
        "mapping_version",
        "entries",
        "review_provenance",
        "manifest_sha256",
    }


def test_policy_schemas_use_exact_section_17_fields() -> None:
    assert set(EvaluationProfile.model_json_schema()["properties"]) == {
        "schema_id",
        "schema_version",
        "evaluation_profile_id",
        "evaluation_profile_version",
        "evaluation_profile_hash",
        "required_experiment_types",
        "required_partitions",
        "required_gate_refs",
        "required_suite_refs",
        "trigger_catalog",
        "runtime_eligible",
        "review_provenance",
    }
    assert set(SuiteDefinition.model_json_schema()["properties"]) == {
        "schema_id",
        "schema_version",
        "suite_id",
        "suite_version",
        "suite_hash",
        "adapter_id",
        "command",
        "input_selector",
        "expected_case_set_hash",
        "critical_invariant_ids",
        "pass_rule",
        "artifact_contract_version",
        "required",
        "review_provenance",
    }
    assert "controlled_variable_keys" in ComparisonPolicy.model_json_schema()["properties"]
    policy_properties = EvaluationPolicy.model_json_schema()["properties"]
    assert {
        "evaluation_profile_ref",
        "comparison_policy_ref",
        "required_partition_refs",
        "required_gate_refs",
        "required_suite_refs",
        "artifact_schema_set_ref",
    }.issubset(policy_properties)


def test_evaluation_context_is_typed_not_path_only() -> None:
    context_schema = EvaluationContext.model_json_schema()["properties"]
    assert context_schema["prescription_fixture"]["anyOf"][0]["$ref"].endswith("/PrescriptionFixture")
    assert context_schema["medication_fixtures"]["items"]["$ref"].endswith("/MedicationFixture")
    assert context_schema["runtime_fixture"]["anyOf"][0]["$ref"].endswith("/RuntimeFixture")


def test_all_five_expected_models_have_the_complete_explicit_null_matrix() -> None:
    expected_fields = {
        "relevant_evidence_refs",
        "required_evidence_refs",
        "gold_claims",
        "forbidden_claims",
        "expected_citations",
        "expected_rule_ids",
        "expected_scope_codes",
        "expected_response_level",
        "expected_safety_disposition",
        "expected_execution_status",
        "expected_release_decision",
        "expected_fallback_code",
        "expected_provider_invocation",
        "expected_retrieval_invocation",
        "expected_publication_allowed",
        "expected_sections",
        "omitted_sections",
        "risk_level",
    }
    assert set(RetrievalExpected.model_json_schema()["properties"]) == expected_fields


def test_loader_reads_each_fixture_file_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.read_bytes
    reads: Counter[Path] = Counter()

    def counted(path: Path) -> bytes:
        reads[path.absolute()] += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert reads
    assert set(reads.values()) == {1}


def test_loaded_authoring_and_policy_graph_is_deeply_immutable() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    with pytest.raises((AttributeError, TypeError)):
        cast(Any, loaded.manifest.case_resources).append(loaded.manifest.case_resources[0])
    with pytest.raises((AttributeError, TypeError)):
        cast(Any, loaded.cases[0].leakage_group_ids)["question_template"] = "SYNTHETIC_MUTATION"
    with pytest.raises((AttributeError, TypeError)):
        cast(Any, loaded.evidence_mapping.entries).append(loaded.evidence_mapping.entries[0])
    with pytest.raises((AttributeError, TypeError)):
        cast(Any, loaded.profile.required_suite_refs).append(loaded.profile.required_suite_refs[0])
    with pytest.raises((AttributeError, TypeError)):
        cast(Any, loaded.cases[0].context.medication_fixtures).append(loaded.cases[0].context.medication_fixtures[0])


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "user_id",
        "profile_id",
        "patient_id",
        "patient_name",
        "patient_birth",
        "birth_date",
        "resident_registration_number",
        "rrn",
        "phone",
        "email",
        "address",
        "medical_document_id",
        "prescription_id",
        "prescription_version_id",
        "ocr_raw",
        "raw_value",
        "normalized_value",
        "llm_draft",
        "structured_output",
        "insurance_code",
        "insurance_code_digest",
        "identifier_digest",
        "provider_request",
        "provider_response",
        "provider_body",
        "api_key",
        "access_token",
        "refresh_token",
        "authorization",
        "credential",
        "secret",
    ],
)
def test_privacy_boundary_rejects_every_section_20_deny_key(forbidden_key: str) -> None:
    with pytest.raises(ValueError):
        validate_privacy_boundary({"safe": {forbidden_key: "SYNTHETIC_SENTINEL"}})


def test_foundation_has_concrete_receipt_and_nonapproved_dataset_provenance() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert PROTECTED_RECEIPT.is_file()
    assert loaded.manifest.status.value == "DRAFT"
    assert loaded.manifest.review_provenance.team_gold_status.value == "REVIEWED"
    assert loaded.manifest.review_provenance.approved_by is None
    assert loaded.manifest.protected_artifact_receipt_ref is not None
    assert loaded.protected_artifact_receipt.resource_set_hash == loaded.manifest.resource_set_hash


def test_foundation_stores_independent_partition_and_resource_hash_claims() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert loaded.manifest.partition_counts.DEV == 5
    assert loaded.manifest.partition_counts.AUTHORING == 0
    assert loaded.manifest.partition_counts.HOLDOUT == 0
    assert loaded.manifest.partition_counts.SAFETY_REGRESSION == 0
    assert loaded.manifest.resource_set_hash


def test_foundation_complete_reference_graph_is_bound() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert loaded.suite.adapter_id == "validation-only.v1"
    assert len(loaded.reference_graph) >= 8
    assert all(reference.resolved for reference in loaded.reference_graph)
