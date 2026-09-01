from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset, load_json_object
from ai_worker.tasks.evaluation.schemas.artifacts import ValidationReceipt
from ai_worker.tasks.evaluation.schemas.authoring import DatasetManifest

REPOSITORY_ROOT = Path(__file__).parents[3]
SOURCE_EVALS = REPOSITORY_ROOT / "evals"
SOURCE_MANIFEST = SOURCE_EVALS / "retrieval/manifests/dev-foundation-v1.dataset.json"


class MutableDatasetFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = root / "retrieval/manifests/dev-foundation-v1.dataset.json"

    @staticmethod
    def read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write(path: Path, value: dict[str, Any]) -> None:
        path.write_bytes(canonical_json_bytes(value) + b"\n")

    @staticmethod
    def refresh_self_hash(value: dict[str, Any]) -> None:
        field = next(
            name
            for name in (
                "manifest_sha256",
                "rubric_hash",
                "snapshot_hash",
                "receipt_hash",
                "evaluation_profile_hash",
                "comparison_policy_hash",
                "evaluation_policy_hash",
                "suite_hash",
            )
            if name in value
        )
        value[field] = canonical_sha256(
            value,
            excluded_top_level_keys=frozenset({field}),
        )

    def manifest_value(self) -> dict[str, Any]:
        return self.read(self.manifest)

    def write_manifest(self, value: dict[str, Any], *, refresh: bool = True) -> None:
        if refresh:
            self.refresh_self_hash(value)
        self.write(self.manifest, value)

    @staticmethod
    def refresh_resource_set_hash(value: dict[str, Any]) -> None:
        value["resource_set_hash"] = canonical_sha256(
            {
                "resources": [
                    {"partition": item["partition"], "path": item["path"], "sha256": item["sha256"]}
                    for item in value["case_resources"]
                ]
            }
        )

    def rebind_case_derived_claims(self, manifest: dict[str, Any]) -> None:
        self.refresh_resource_set_hash(manifest)
        receipt_path = self.root / "provenance/dev-foundation-v1.protected-artifact-receipt.json"
        receipt = self.read(receipt_path)
        receipt["resource_set_hash"] = manifest["resource_set_hash"]
        receipt["artifact_paths"] = [item["path"] for item in manifest["case_resources"]]
        self.refresh_self_hash(receipt)
        self.write(receipt_path, receipt)
        manifest["protected_artifact_receipt_ref"]["hash"] = sha256_hex(receipt_path.read_bytes())

        policy_path = self.root / "policies/dev-foundation-v1.evaluation-policy.json"
        policy = self.read(policy_path)
        for member in policy["required_partition_refs"]:
            partition = member["reference"]["id"].rsplit(":", 1)[1]
            resources = [
                {"case_id": item["case_id"], "path": item["path"], "sha256": item["sha256"]}
                for item in manifest["case_resources"]
                if item["partition"] == partition
            ]
            member["reference"]["hash"] = canonical_sha256(cast(Any, {"partition": partition, "resources": resources}))
        members = [
            policy["evaluation_profile_ref"],
            policy["comparison_policy_ref"],
            *policy["required_partition_refs"],
            *policy["required_gate_refs"],
            *policy["required_suite_refs"],
            policy["artifact_schema_set_ref"],
        ]
        policy["member_manifest_hash"] = canonical_sha256({"members": members})
        self.refresh_self_hash(policy)
        self.write(policy_path, policy)

    def case_path(self, case_id: str) -> Path:
        manifest = self.manifest_value()
        resource = next(item for item in manifest["case_resources"] if item["case_id"] == case_id)
        return self.root / resource["path"]

    def mutate_case(self, case_id: str, mutation: Any) -> None:
        manifest = self.manifest_value()
        resource = next(item for item in manifest["case_resources"] if item["case_id"] == case_id)
        path = self.root / resource["path"]
        value = self.read(path)
        mutation(value)
        if "query" in value and "context" in value:
            value["input_sha256"] = canonical_sha256({"query": value["query"], "context": value["context"]})
        self.write(path, value)
        resource["sha256"] = sha256_hex(path.read_bytes())
        self.rebind_case_derived_claims(manifest)
        self.write_manifest(manifest)

    def mutate_resource(self, key: str, mutation: Any) -> None:
        manifest = self.manifest_value()
        paths = {
            "evidence_mapping": "retrieval/evidence/dev-foundation-v1.evidence-mapping.json",
            "critical_claim_rubric": "retrieval/manifests/dev-foundation-v1.critical-claim-rubric.json",
        }
        path = self.root / paths[key]
        value = self.read(path)
        mutation(value)
        self.refresh_self_hash(value)
        self.write(path, value)
        if key == "evidence_mapping":
            manifest["evidence_mapping_manifest_sha256"] = value["manifest_sha256"]
        else:
            rubric_ref = {
                "id": value["rubric_id"],
                "version": value["rubric_version"],
                "hash": value["rubric_hash"],
            }
            manifest["critical_claim_rubric_ref"] = rubric_ref
            for resource in manifest["case_resources"]:
                case_path = self.root / resource["path"]
                case = self.read(case_path)
                case["critical_claim_rubric_ref"] = rubric_ref
                self.write(case_path, case)
                resource["sha256"] = sha256_hex(case_path.read_bytes())
            manifest["resource_set_hash"] = canonical_sha256(
                {
                    "resources": [
                        {"partition": item["partition"], "path": item["path"], "sha256": item["sha256"]}
                        for item in manifest["case_resources"]
                    ]
                }
            )
        self.write_manifest(manifest)

    def mutate_config(self, relative_path: str, mutation: Any) -> None:
        path = self.root / relative_path
        value = self.read(path)
        mutation(value)
        self.refresh_self_hash(value)
        self.write(path, value)

    def rebind_configuration_refs(self) -> None:
        suite_path = self.root / "suites/dev-foundation-v1.suite.json"
        suite = self.read(suite_path)
        profile_path = self.root / "profiles/dev-foundation-v1.profile.json"
        profile = self.read(profile_path)
        for reference in profile["required_suite_refs"]:
            if reference["id"] == suite["suite_id"]:
                reference["hash"] = suite["suite_hash"]
        self.refresh_self_hash(profile)
        self.write(profile_path, profile)

        policy_path = self.root / "policies/dev-foundation-v1.evaluation-policy.json"
        policy = self.read(policy_path)
        policy["evaluation_profile_ref"]["reference"]["hash"] = profile["evaluation_profile_hash"]
        for member in policy["required_suite_refs"]:
            if member["reference"]["id"] == suite["suite_id"]:
                member["reference"]["hash"] = suite["suite_hash"]
        members = [
            policy["evaluation_profile_ref"],
            policy["comparison_policy_ref"],
            *policy["required_partition_refs"],
            *policy["required_gate_refs"],
            *policy["required_suite_refs"],
            policy["artifact_schema_set_ref"],
        ]
        policy["member_manifest_hash"] = canonical_sha256({"members": members})
        self.refresh_self_hash(policy)
        self.write(policy_path, policy)


@pytest.fixture
def tmp_dataset(tmp_path: Path) -> MutableDatasetFixture:
    root = tmp_path / "evals"
    shutil.copytree(SOURCE_EVALS, root)
    return MutableDatasetFixture(root)


def test_load_json_object_rejects_invalid_json_without_echoing_input(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(b'{"secret":"SECRET_SENTINEL",}')

    with pytest.raises(EvaluationValidationError) as caught:
        load_json_object(path, DatasetManifest)

    assert caught.value.code is EvaluationErrorCode.JSON_INVALID
    assert "SECRET_SENTINEL" not in str(caught.value)


def test_load_json_object_rejects_duplicate_keys_without_echoing_input(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_bytes(b'{"dataset_code":"safe","dataset_code":"SECRET_SENTINEL"}')

    with pytest.raises(EvaluationValidationError) as caught:
        load_json_object(path, DatasetManifest)

    assert caught.value.code is EvaluationErrorCode.JSON_DUPLICATE_KEY
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize(
    "raw_bytes",
    [
        b'{"value":"SECRET_SENTINEL"}\xff',
        b'{"value":1.5,"safe":"SECRET_SENTINEL"}',
        b'{"value":"\\ud800","safe":"SECRET_SENTINEL"}',
    ],
)
def test_load_json_object_rejects_non_utf8_or_non_ijson_bytes(
    tmp_path: Path,
    raw_bytes: bytes,
) -> None:
    path = tmp_path / "non-ijson.json"
    path.write_bytes(raw_bytes)

    with pytest.raises(EvaluationValidationError) as caught:
        load_json_object(path, DatasetManifest)

    assert caught.value.code is EvaluationErrorCode.JSON_INVALID
    assert "SECRET_SENTINEL" not in str(caught.value)


def test_load_json_object_wraps_schema_errors_without_raw_pydantic_details(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text('{"dataset_code":"SECRET_SENTINEL"}', encoding="utf-8")

    with pytest.raises(EvaluationValidationError) as caught:
        load_json_object(path, DatasetManifest)

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID
    assert "SECRET_SENTINEL" not in str(caught.value)


def assert_dataset_error(
    fixture: MutableDatasetFixture,
    code: EvaluationErrorCode,
) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        load_dataset(fixture.manifest, evals_root=fixture.root)
    assert caught.value.code is code
    assert "SECRET_SENTINEL" not in str(caught.value)


def test_loader_rejects_manifest_digest_format(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["manifest_sha256"] = "SECRET_SENTINEL"
    tmp_dataset.write_manifest(manifest, refresh=False)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.HASH_INVALID)


def test_loader_rejects_manifest_self_hash_mismatch(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["dataset_code"] = "synthetic-mutated"
    tmp_dataset.write_manifest(manifest, refresh=False)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.HASH_MISMATCH)


def test_loader_rejects_missing_resource(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["case_resources"][0]["path"] = "retrieval/cases/dev-foundation-v1/missing.json"
    tmp_dataset.refresh_resource_set_hash(manifest)
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.RESOURCE_MISSING)


def test_loader_rejects_invalid_resource_path(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["case_resources"][0]["path"] = "../SECRET_SENTINEL.json"
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.RESOURCE_PATH_INVALID)


def test_loader_rejects_resource_hash_mismatch(tmp_dataset: MutableDatasetFixture) -> None:
    path = tmp_dataset.case_path("rag-dev-retrieval-001")
    path.write_bytes(path.read_bytes() + b" ")
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.HASH_MISMATCH)


def test_loader_rejects_unresolved_context_reference(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["context"]["runtime_fixture"]["source_snapshot_ref"].__setitem__("id", "SECRET_SENTINEL"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_manifest_outside_evals_root(tmp_dataset: MutableDatasetFixture) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        load_dataset(SOURCE_MANIFEST, evals_root=tmp_dataset.root)
    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_loader_rejects_symlink_resource(tmp_dataset: MutableDatasetFixture) -> None:
    path = tmp_dataset.case_path("rag-dev-retrieval-001")
    path.unlink()
    path.symlink_to(SOURCE_EVALS / path.relative_to(tmp_dataset.root))
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.RESOURCE_PATH_INVALID)


@pytest.mark.parametrize("duplicate_field", ["case_id", "path"])
def test_loader_rejects_duplicate_case_identity_or_path(
    tmp_dataset: MutableDatasetFixture,
    duplicate_field: str,
) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["case_resources"][1][duplicate_field] = manifest["case_resources"][0][duplicate_field]
    tmp_dataset.refresh_resource_set_hash(manifest)
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.CASE_DUPLICATE)


def test_loader_rejects_invalid_partition(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["case_resources"][0]["partition"] = "SECRET_SENTINEL"
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.PARTITION_INVALID)


def test_loader_rejects_partition_count_mismatch(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["case_resources"][-1]["partition"] = "HOLDOUT"
    tmp_dataset.refresh_resource_set_hash(manifest)
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.PARTITION_COUNT_MISMATCH)


def test_loader_rejects_independent_resource_set_hash_claim(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["resource_set_hash"] = "a" * 64
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.HASH_MISMATCH)


@pytest.mark.parametrize(
    "axis",
    ["question_template", "source_segment", "medication_family", "transform_origin"],
)
def test_loader_rejects_each_cross_partition_leakage_axis(
    tmp_dataset: MutableDatasetFixture,
    axis: str,
) -> None:
    first = tmp_dataset.read(tmp_dataset.case_path("rag-dev-retrieval-001"))
    shared = first["leakage_group_ids"][axis]
    tmp_dataset.mutate_case(
        "rag-dev-safety-001",
        lambda case: (
            case.__setitem__("partition", "HOLDOUT"),
            case["leakage_group_ids"].__setitem__(axis, shared),
        ),
    )
    manifest = tmp_dataset.manifest_value()
    resource = next(item for item in manifest["case_resources"] if item["case_id"] == "rag-dev-safety-001")
    resource["partition"] = "HOLDOUT"
    manifest["partition_counts"] = {"AUTHORING": 0, "DEV": 4, "HOLDOUT": 1, "SAFETY_REGRESSION": 0}
    manifest["resource_set_hash"] = canonical_sha256(
        {
            "resources": [
                {"partition": item["partition"], "path": item["path"], "sha256": item["sha256"]}
                for item in manifest["case_resources"]
            ]
        }
    )
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.LEAKAGE_CROSS_PARTITION)


def test_loader_rejects_rubric_claim_mismatch(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_resource(
        "critical_claim_rubric",
        lambda rubric: rubric["applicable_task_types"].remove("RETRIEVAL"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.RUBRIC_MISMATCH)


def test_loader_rejects_unmapped_evidence_reference(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["expected"]["relevant_evidence_refs"].append("ev-synthetic-unmapped-001"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def test_loader_rejects_duplicate_evidence_mapping_id(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_resource(
        "evidence_mapping",
        lambda mapping: mapping["entries"].append(mapping["entries"][0]),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


@pytest.mark.parametrize(
    "resource_key",
    ["evidence_mapping", "critical_claim_rubric"],
)
def test_loader_rejects_resource_self_hash_mismatch(
    tmp_dataset: MutableDatasetFixture,
    resource_key: str,
) -> None:
    paths = {
        "evidence_mapping": "retrieval/evidence/dev-foundation-v1.evidence-mapping.json",
        "critical_claim_rubric": "retrieval/manifests/dev-foundation-v1.critical-claim-rubric.json",
    }
    path = tmp_dataset.root / paths[resource_key]
    value = tmp_dataset.read(path)
    hash_field = "manifest_sha256" if resource_key == "evidence_mapping" else "rubric_hash"
    value[hash_field] = "a" * 64
    tmp_dataset.write(path, value)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.HASH_MISMATCH)


def test_loader_rejects_duplicate_evidence_reference_in_case(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["expected"]["relevant_evidence_refs"].append("ev-synthetic-chunk-001"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_forbidden_privacy_key(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case.__setitem__("ocr_raw", "SECRET_SENTINEL"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_privacy_value(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case.__setitem__("query", "patient@example.com SECRET_SENTINEL"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.PRIVACY_VALUE_DETECTED)


@pytest.mark.parametrize(
    ("parameter", "expected_code"),
    [
        ({"patient_id": "SYNTHETIC_SENTINEL"}, EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN),
        ({"safe_parameter": "patient@example.com"}, EvaluationErrorCode.PRIVACY_VALUE_DETECTED),
        (
            {"patient_id": ["SYNTHETIC_SENTINEL"]},
            EvaluationErrorCode.MANIFEST_INVALID,
        ),
    ],
)
def test_loader_validates_flexible_policy_parameters_structurally_then_for_privacy(
    tmp_dataset: MutableDatasetFixture,
    parameter: dict[str, object],
    expected_code: EvaluationErrorCode,
) -> None:
    tmp_dataset.mutate_config(
        "policies/dev-foundation-v1.comparison-policy.json",
        lambda value: value["scopes"][0].__setitem__("ci_parameters", parameter),
    )
    assert_dataset_error(tmp_dataset, expected_code)


def test_loader_requires_deidentification_approval(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["data_classification"] = "APPROVED_DEIDENTIFIED"
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.DEIDENTIFICATION_APPROVAL_REQUIRED)


def test_loader_rejects_review_provenance_self_approval(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["review_provenance"]["approved_by"] = manifest["review_provenance"]["authored_by"]
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)


def test_load_json_object_rejects_invalid_state_combination(tmp_path: Path) -> None:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.validation-receipt",
        "schema_version": "1.0.0",
        "validation_id": "123e4567-e89b-12d3-a456-426614174000",
        "validated_at": "2026-09-01T00:01:00.000000Z",
        "validator_version": "1.0.0",
        "manifest_path": "datasets/manifest.json",
        "dataset_code": "rag-foundation",
        "dataset_version": "1.0.0",
        "dataset_manifest_sha256": "a" * 64,
        "evaluation_profile_ref": None,
        "comparison_policy_ref": None,
        "execution_status": "INVALID",
        "decision_status": "PASS",
        "release_eligible": False,
        "error_codes": ["SECRET_SENTINEL"],
        "invalid_resource_paths": [],
    }
    path = tmp_path / "receipt.json"
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(EvaluationValidationError) as caught:
        load_json_object(path, ValidationReceipt)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID
    assert "SECRET_SENTINEL" not in str(caught.value)


def test_loader_rejects_profile_suite_hash_mismatch(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_config(
        "profiles/dev-foundation-v1.profile.json",
        lambda value: value["required_suite_refs"][0].__setitem__("hash", "a" * 64),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_evaluation_policy_member_hash_mismatch(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.mutate_config(
        "policies/dev-foundation-v1.evaluation-policy.json",
        lambda value: value["evaluation_profile_ref"]["reference"].__setitem__("hash", "a" * 64),
    )
    value = tmp_dataset.read(tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json")
    members = [
        value["evaluation_profile_ref"],
        value["comparison_policy_ref"],
        *value["required_partition_refs"],
        *value["required_gate_refs"],
        *value["required_suite_refs"],
        value["artifact_schema_set_ref"],
    ]
    value["member_manifest_hash"] = canonical_sha256({"members": members})
    tmp_dataset.refresh_self_hash(value)
    tmp_dataset.write(tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json", value)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize(
    "reference_field",
    [
        "source_snapshot_ref",
        "knowledge_index_ref",
        "rule_set_ref",
        "guideline_set_ref",
        "safety_policy_set_ref",
    ],
)
def test_loader_resolves_every_runtime_context_reference(
    tmp_dataset: MutableDatasetFixture,
    reference_field: str,
) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["context"]["runtime_fixture"][reference_field].__setitem__("hash", "a" * 64),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize(
    "member_field",
    [
        "evaluation_profile_ref",
        "comparison_policy_ref",
        "required_partition_refs",
        "required_suite_refs",
        "artifact_schema_set_ref",
    ],
)
def test_loader_resolves_every_evaluation_policy_reference(
    tmp_dataset: MutableDatasetFixture,
    member_field: str,
) -> None:
    path = tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json"
    value = tmp_dataset.read(path)
    target = value[member_field]
    member = target[0] if isinstance(target, list) else target
    member["reference"]["hash"] = "a" * 64
    members = [
        value["evaluation_profile_ref"],
        value["comparison_policy_ref"],
        *value["required_partition_refs"],
        *value["required_gate_refs"],
        *value["required_suite_refs"],
        value["artifact_schema_set_ref"],
    ]
    value["member_manifest_hash"] = canonical_sha256({"members": members})
    tmp_dataset.refresh_self_hash(value)
    tmp_dataset.write(path, value)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_binds_protected_receipt_to_exact_resource_set(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    receipt_path = tmp_dataset.root / "provenance/dev-foundation-v1.protected-artifact-receipt.json"
    receipt = tmp_dataset.read(receipt_path)
    receipt["resource_set_hash"] = "a" * 64
    tmp_dataset.refresh_self_hash(receipt)
    tmp_dataset.write(receipt_path, receipt)
    manifest = tmp_dataset.manifest_value()
    manifest["protected_artifact_receipt_ref"]["hash"] = sha256_hex(receipt_path.read_bytes())
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_binds_protected_receipt_reference_to_exact_file_bytes(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["protected_artifact_receipt_ref"]["hash"] = "a" * 64
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize(
    ("relative_path", "mutation"),
    [
        (
            "profiles/dev-foundation-v1.profile.json",
            lambda value: value["required_suite_refs"].append(value["required_suite_refs"][0]),
        ),
        (
            "suites/dev-foundation-v1.suite.json",
            lambda value: value["input_selector"]["task_types"].append(value["input_selector"]["task_types"][0]),
        ),
        (
            "policies/dev-foundation-v1.comparison-policy.json",
            lambda value: value["scopes"].append(value["scopes"][0]),
        ),
    ],
)
def test_loader_rejects_duplicate_configuration_natural_keys(
    tmp_dataset: MutableDatasetFixture,
    relative_path: str,
    mutation: Any,
) -> None:
    tmp_dataset.mutate_config(relative_path, mutation)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize(
    ("selector_field", "selector_value"),
    [
        ("partitions", ["HOLDOUT"]),
        ("task_types", ["RETRIEVAL"]),
    ],
)
def test_loader_binds_every_suite_selector_field_to_selected_cases(
    tmp_dataset: MutableDatasetFixture,
    selector_field: str,
    selector_value: list[str],
) -> None:
    tmp_dataset.mutate_config(
        "suites/dev-foundation-v1.suite.json",
        lambda value: value["input_selector"].__setitem__(selector_field, selector_value),
    )
    tmp_dataset.rebind_configuration_refs()
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_prioritizes_case_structure_over_stale_exact_byte_hash(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    case_path = tmp_dataset.case_path("rag-dev-retrieval-001")
    case = tmp_dataset.read(case_path)
    del case["query"]
    tmp_dataset.write(case_path, case)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


@pytest.mark.parametrize(
    ("target_field", "source_field"),
    [
        ("evaluation_profile_ref", "comparison_policy_ref"),
        ("comparison_policy_ref", "evaluation_profile_ref"),
        ("required_partition_refs", "required_suite_refs"),
        ("required_suite_refs", "required_partition_refs"),
        ("artifact_schema_set_ref", "required_suite_refs"),
    ],
)
def test_loader_rejects_policy_reference_with_valid_tuple_of_wrong_kind(
    tmp_dataset: MutableDatasetFixture,
    target_field: str,
    source_field: str,
) -> None:
    path = tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json"
    value = tmp_dataset.read(path)
    target = value[target_field]
    source = value[source_field]
    target_member = target[0] if isinstance(target, list) else target
    source_member = source[0] if isinstance(source, list) else source
    target_member["reference"] = source_member["reference"]
    members = [
        value["evaluation_profile_ref"],
        value["comparison_policy_ref"],
        *value["required_partition_refs"],
        *value["required_gate_refs"],
        *value["required_suite_refs"],
        value["artifact_schema_set_ref"],
    ]
    value["member_manifest_hash"] = canonical_sha256({"members": members})
    tmp_dataset.refresh_self_hash(value)
    tmp_dataset.write(path, value)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_gate_reference_with_valid_suite_tuple(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    path = tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json"
    value = tmp_dataset.read(path)
    suite_member = value["required_suite_refs"][0]
    value["required_gate_refs"] = [{"member_order": 4, "member_type": "GATE", "reference": suite_member["reference"]}]
    suite_member["member_order"] = 5
    value["artifact_schema_set_ref"]["member_order"] = 6
    members = [
        value["evaluation_profile_ref"],
        value["comparison_policy_ref"],
        *value["required_partition_refs"],
        *value["required_gate_refs"],
        *value["required_suite_refs"],
        value["artifact_schema_set_ref"],
    ]
    value["member_manifest_hash"] = canonical_sha256({"members": members})
    tmp_dataset.refresh_self_hash(value)
    tmp_dataset.write(path, value)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_profile_suite_reference_with_valid_comparison_tuple(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    comparison = tmp_dataset.read(tmp_dataset.root / "policies/dev-foundation-v1.comparison-policy.json")
    profile_path = tmp_dataset.root / "profiles/dev-foundation-v1.profile.json"
    profile = tmp_dataset.read(profile_path)
    profile["required_suite_refs"][0] = {
        "id": comparison["comparison_policy_id"],
        "version": comparison["comparison_policy_version"],
        "hash": comparison["comparison_policy_hash"],
    }
    tmp_dataset.refresh_self_hash(profile)
    tmp_dataset.write(profile_path, profile)
    tmp_dataset.rebind_configuration_refs()

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize(
    ("target_field", "source_field"),
    [
        ("source_snapshot_ref", "knowledge_index_ref"),
        ("knowledge_index_ref", "rule_set_ref"),
        ("rule_set_ref", "guideline_set_ref"),
        ("guideline_set_ref", "safety_policy_set_ref"),
        ("safety_policy_set_ref", "knowledge_index_ref"),
    ],
)
def test_loader_rejects_runtime_reference_with_valid_tuple_of_wrong_kind(
    tmp_dataset: MutableDatasetFixture,
    target_field: str,
    source_field: str,
) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["context"]["runtime_fixture"].__setitem__(
            target_field,
            case["context"]["runtime_fixture"][source_field],
        ),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_schema_set_hash_is_independent_of_schema_file_path_order(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    first = tmp_dataset.root / "schemas/1.0.0/authoring/rag-eval.case.schema.json"
    second = tmp_dataset.root / "schemas/1.0.0/authoring/rag-eval.dataset-manifest.schema.json"
    temporary = tmp_dataset.root / "schemas/1.0.0/authoring/SYNTHETIC_SWAP.schema.json"
    first.rename(temporary)
    second.rename(first)
    temporary.rename(second)

    load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)
