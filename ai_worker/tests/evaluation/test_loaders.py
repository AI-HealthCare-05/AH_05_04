from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

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
    def refresh_content_hash(value: dict[str, Any]) -> None:
        value["content_hash"] = canonical_sha256(
            value,
            excluded_top_level_keys=frozenset({"content_hash"}),
        )

    def manifest_value(self) -> dict[str, Any]:
        return self.read(self.manifest)

    def write_manifest(self, value: dict[str, Any], *, refresh: bool = True) -> None:
        if refresh:
            self.refresh_content_hash(value)
        self.write(self.manifest, value)

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
        if "question" in value and "context" in value:
            value["input_hash"] = canonical_sha256({"question": value["question"], "context": value["context"]})
        self.write(path, value)
        resource["sha256"] = sha256_hex(path.read_bytes())
        self.write_manifest(manifest)

    def mutate_resource(self, key: str, mutation: Any) -> None:
        manifest = self.manifest_value()
        reference = manifest[key]
        path = self.root / reference["path"]
        value = self.read(path)
        mutation(value)
        self.refresh_content_hash(value)
        self.write(path, value)
        reference["sha256"] = sha256_hex(path.read_bytes())
        self.write_manifest(manifest)

    def mutate_config(self, relative_path: str, mutation: Any) -> None:
        path = self.root / relative_path
        value = self.read(path)
        mutation(value)
        self.refresh_content_hash(value)
        self.write(path, value)


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
    manifest["content_hash"] = "SECRET_SENTINEL"
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


def test_loader_rejects_missing_context_resource(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["context"].__setitem__(
            "prescription_fixture",
            "retrieval/evidence/resources/dev-foundation-v1/SECRET_SENTINEL.json",
        ),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.RESOURCE_MISSING)


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
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.CASE_DUPLICATE)


def test_loader_rejects_invalid_partition(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["case_resources"][0]["partition"] = "SECRET_SENTINEL"
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.PARTITION_INVALID)


def test_loader_rejects_partition_count_mismatch(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["case_resources"][0]["partition"] = "HOLDOUT"
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.PARTITION_COUNT_MISMATCH)


@pytest.mark.parametrize(
    "axis",
    ["question_template", "source_segment", "medication_family", "transform_origin"],
)
def test_loader_rejects_each_cross_partition_leakage_axis(
    tmp_dataset: MutableDatasetFixture,
    axis: str,
) -> None:
    first = tmp_dataset.read(tmp_dataset.case_path("rag-dev-retrieval-001"))
    shared = first["leakage_groups"][axis]
    tmp_dataset.mutate_case(
        "rag-dev-safety-001",
        lambda case: (case.__setitem__("partition", "HOLDOUT"), case["leakage_groups"].__setitem__(axis, shared)),
    )
    manifest = tmp_dataset.manifest_value()
    resource = next(item for item in manifest["case_resources"] if item["case_id"] == "rag-dev-safety-001")
    resource["partition"] = "HOLDOUT"
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.LEAKAGE_CROSS_PARTITION)


def test_loader_rejects_rubric_claim_mismatch(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_resource(
        "critical_claim_rubric",
        lambda rubric: rubric["critical_claim_keys"].append("synthetic-unbound-claim"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.RUBRIC_MISMATCH)


def test_loader_rejects_unmapped_evidence_reference(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["expected"]["gold_evidence_ids"].append("ev-synthetic-unmapped-001"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def test_loader_rejects_duplicate_evidence_mapping_id(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_resource(
        "evidence_mapping",
        lambda mapping: mapping["evidence"].append(mapping["evidence"][0]),
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
    manifest = tmp_dataset.manifest_value()
    reference = manifest[resource_key]
    path = tmp_dataset.root / reference["path"]
    value = tmp_dataset.read(path)
    value["content_hash"] = "a" * 64
    tmp_dataset.write(path, value)
    reference["sha256"] = sha256_hex(path.read_bytes())
    tmp_dataset.write_manifest(manifest)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.HASH_MISMATCH)


def test_loader_rejects_duplicate_evidence_reference_in_case(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case["expected"]["gold_evidence_ids"].append("ev-synthetic-chunk-001"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def test_loader_rejects_forbidden_privacy_key(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case.__setitem__("ocr_raw", "SECRET_SENTINEL"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN)


def test_loader_rejects_privacy_value(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-retrieval-001",
        lambda case: case.__setitem__("question", "patient@example.com SECRET_SENTINEL"),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.PRIVACY_VALUE_DETECTED)


def test_loader_requires_deidentification_approval(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["content_classification"] = "APPROVED_DEIDENTIFIED"
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
        lambda value: value["suite_references"][0].__setitem__("hash", "a" * 64),
    )
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_evaluation_policy_member_hash_mismatch(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.mutate_config(
        "policies/dev-foundation-v1.evaluation-policy.json",
        lambda value: value["members"][0]["reference"].__setitem__("hash", "a" * 64),
    )
    value = tmp_dataset.read(tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json")
    value["member_manifest_hash"] = canonical_sha256({"members": value["members"]})
    tmp_dataset.refresh_content_hash(value)
    tmp_dataset.write(tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json", value)
    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize(
    ("relative_path", "mutation"),
    [
        (
            "profiles/dev-foundation-v1.profile.json",
            lambda value: value["suite_references"].append(value["suite_references"][0]),
        ),
        (
            "suites/dev-foundation-v1.suite.json",
            lambda value: value["task_types"].append(value["task_types"][0]),
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
