from __future__ import annotations

import errno
import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation import loaders as evaluation_loaders
from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset, load_json_object
from ai_worker.tasks.evaluation.schema_exports import write_schema_documents
from ai_worker.tasks.evaluation.schema_registry import SCHEMA_REGISTRIES
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

    @staticmethod
    def _set_team_gold(provenance: dict[str, Any], *, approved: bool, role: str) -> None:
        provenance["team_gold_status"] = "APPROVED" if approved else "REVIEWED"
        provenance["approved_by"] = (
            {
                "namespace": "EXTERNAL_APPROVAL_REGISTRY",
                "actor_id": f"synthetic-{role.lower().replace('_', '-')}",
                "role": role,
            }
            if approved
            else None
        )
        provenance["approved_at"] = "2026-09-02T00:02:00.000000Z" if approved else None

    def _schema_set_hash(self, version: str) -> str:
        entries = [
            {
                "schema_id": entry.schema_id,
                "schema_version": entry.member_version,
                "schema_sha256": sha256_hex((self.root / "schemas" / version / entry.relative_path).read_bytes()),
            }
            for entry in SCHEMA_REGISTRIES[version]
        ]
        entries.sort(key=lambda item: (item["schema_id"], item["schema_version"], item["schema_sha256"]))
        return canonical_sha256(cast(Any, {"schemas": entries}))

    def upgrade_to_v1_1(self, *, frozen: bool = False, reviewed_child: str | None = None) -> None:
        manifest = self.manifest_value()
        evidence_path = self.root / "retrieval/evidence/dev-foundation-v1.evidence-mapping.json"
        evidence = self.read(evidence_path)
        self._set_team_gold(
            evidence["review_provenance"],
            approved=not frozen or reviewed_child != "evidence_mapping",
            role="DATASET_CUSTODIAN",
        )
        self.refresh_self_hash(evidence)
        self.write(evidence_path, evidence)

        rubric_path = self.root / "retrieval/manifests/dev-foundation-v1.critical-claim-rubric.json"
        rubric = self.read(rubric_path)
        self._set_team_gold(
            rubric["review_provenance"],
            approved=not frozen or reviewed_child != "critical_claim_rubric",
            role="PRODUCT_SAFETY_REVIEWER",
        )
        self.refresh_self_hash(rubric)
        self.write(rubric_path, rubric)

        rubric_ref = {
            "id": rubric["rubric_id"],
            "version": rubric["rubric_version"],
            "hash": rubric["rubric_hash"],
        }
        for resource in manifest["case_resources"]:
            case_path = self.root / resource["path"]
            case = self.read(case_path)
            case["schema_version"] = "1.1.0"
            case["critical_claim_rubric_ref"] = rubric_ref
            runtime = case["context"]["runtime_fixture"]
            runtime.update(
                source_eligibility_status="ELIGIBLE",
                bundle_eligibility_status="ELIGIBLE",
                dependency_fault="NONE",
            )
            runtime["source_snapshot_ref"]["hash"] = evidence["manifest_sha256"]
            if case["task_type"] in {"SAFETY", "END_TO_END_RAG"}:
                case["expected"].update(
                    expected_rule_outcome="MATCHED_RULES",
                    expected_rule_not_invoked_reason=None,
                )
            else:
                case["expected"].update(
                    expected_rule_outcome=None,
                    expected_rule_not_invoked_reason=None,
                )
            self._set_team_gold(
                case["review_provenance"],
                approved=not frozen or reviewed_child != "case" or case["case_id"] != "rag-dev-safety-001",
                role="PRODUCT_SAFETY_REVIEWER",
            )
            case["input_sha256"] = canonical_sha256({"query": case["query"], "context": case["context"]})
            self.write(case_path, case)
            resource["sha256"] = sha256_hex(case_path.read_bytes())

        manifest.update(
            schema_version="1.1.0",
            evidence_mapping_manifest_sha256=evidence["manifest_sha256"],
            critical_claim_rubric_ref=rubric_ref,
            evaluation_corpus_snapshot_ref={
                "id": evidence["mapping_id"],
                "version": evidence["mapping_version"],
                "hash": evidence["manifest_sha256"],
            },
        )
        if frozen:
            manifest.update(status="FROZEN", frozen_at="2026-09-02T00:03:00.000000Z")
            self._set_team_gold(manifest["review_provenance"], approved=True, role="DATASET_CUSTODIAN")
        self.rebind_case_derived_claims(manifest)

        policy_path = self.root / "policies/dev-foundation-v1.evaluation-policy.json"
        policy = self.read(policy_path)
        policy["artifact_schema_set_ref"]["reference"].update(
            version="1.1.0",
            hash=self._schema_set_hash("1.1.0"),
        )
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
        self.write_manifest(manifest)

    @staticmethod
    def _set_v1_2_draft(provenance: dict[str, Any]) -> None:
        provenance.update(
            reviewed_by=None,
            approved_by=None,
            reviewed_at=None,
            approved_at=None,
            team_gold_status="DRAFT",
            evidence_review_refs=[],
        )

    def upgrade_to_v1_2(self) -> None:
        self.upgrade_to_v1_1()
        write_schema_documents(self.root / "schemas/1.2.0", "1.2.0")

        manifest = self.manifest_value()
        evidence_path = self.root / "retrieval/evidence/dev-foundation-v1.evidence-mapping.json"
        evidence = self.read(evidence_path)
        evidence.update(schema_version="1.2.0")
        self._set_v1_2_draft(evidence["review_provenance"])
        self.refresh_self_hash(evidence)
        self.write(evidence_path, evidence)

        rubric_path = self.root / "retrieval/manifests/dev-foundation-v1.critical-claim-rubric.json"
        rubric = self.read(rubric_path)
        rubric.update(schema_version="1.2.0")
        self._set_v1_2_draft(rubric["review_provenance"])
        self.refresh_self_hash(rubric)
        self.write(rubric_path, rubric)

        rubric_ref = {"id": rubric["rubric_id"], "version": rubric["rubric_version"], "hash": rubric["rubric_hash"]}
        for resource in manifest["case_resources"]:
            case_path = self.root / resource["path"]
            case = self.read(case_path)
            case.update(schema_version="1.2.0", critical_claim_rubric_ref=rubric_ref)
            case["context"]["runtime_fixture"]["source_snapshot_ref"]["hash"] = evidence["manifest_sha256"]
            self._set_v1_2_draft(case["review_provenance"])
            case["input_sha256"] = canonical_sha256({"query": case["query"], "context": case["context"]})
            self.write(case_path, case)
            resource["sha256"] = sha256_hex(case_path.read_bytes())

        manifest.update(
            schema_version="1.2.0",
            evidence_mapping_manifest_sha256=evidence["manifest_sha256"],
            critical_claim_rubric_ref=rubric_ref,
            evaluation_corpus_snapshot_ref={
                "id": evidence["mapping_id"],
                "version": evidence["mapping_version"],
                "hash": evidence["manifest_sha256"],
            },
        )
        self._set_v1_2_draft(manifest["review_provenance"])
        self.rebind_case_derived_claims(manifest)
        receipt_path = self.root / "provenance/dev-foundation-v1.protected-artifact-receipt.json"
        receipt = self.read(receipt_path)
        receipt["schema_version"] = "1.2.0"
        self._set_v1_2_draft(receipt["recorded_by"])
        self.refresh_self_hash(receipt)
        self.write(receipt_path, receipt)
        manifest["protected_artifact_receipt_ref"]["hash"] = sha256_hex(receipt_path.read_bytes())

        for relative_path in (
            "profiles/dev-foundation-v1.profile.json",
            "suites/dev-foundation-v1.suite.json",
            "policies/dev-foundation-v1.evaluation-policy.json",
        ):
            self.mutate_config(
                relative_path,
                lambda value: (
                    value.update(schema_version="1.2.0"),
                    self._set_v1_2_draft(value["review_provenance"]),
                ),
            )
        self.rebind_configuration_refs()

        policy_path = self.root / "policies/dev-foundation-v1.evaluation-policy.json"
        policy = self.read(policy_path)
        policy["artifact_schema_set_ref"]["reference"].update(
            version="1.2.0",
            hash=self._schema_set_hash("1.2.0"),
        )
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
        self.write_manifest(manifest)


@pytest.fixture
def tmp_dataset(tmp_path: Path) -> MutableDatasetFixture:
    root = tmp_path / "evals"
    shutil.copytree(SOURCE_EVALS, root)
    return MutableDatasetFixture(root)


def test_loader_dispatches_schema_set_1_1_without_breaking_v1_fixture(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    original = load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)
    tmp_dataset.upgrade_to_v1_1()

    upgraded = load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)

    assert original.manifest.schema_version == "1.0.0"
    assert upgraded.manifest.schema_version == "1.1.0"
    assert upgraded.cases[0].schema_version == "1.1.0"
    assert any(
        reference.kind == "ARTIFACT_SCHEMA_SET" and reference.version == "1.1.0"
        for reference in upgraded.reference_graph
    )


def test_loader_dispatches_complete_schema_set_1_2_graph(tmp_dataset: MutableDatasetFixture) -> None:
    tmp_dataset.upgrade_to_v1_2()

    loaded = load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)

    assert loaded.manifest.schema_version == "1.2.0"
    assert {case.schema_version for case in loaded.cases} == {"1.2.0"}
    assert loaded.evidence_mapping.schema_version == "1.2.0"
    assert loaded.rubric.schema_version == "1.2.0"
    assert loaded.profile.schema_version == "1.2.0"
    assert loaded.evaluation_policy.schema_version == "1.2.0"
    assert loaded.suite.schema_version == "1.2.0"
    assert loaded.protected_artifact_receipt is not None
    assert loaded.protected_artifact_receipt.schema_version == "1.2.0"


def test_loader_compares_payload_versions_with_authoring_members_not_set_version(
    tmp_dataset: MutableDatasetFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_dataset.upgrade_to_v1_1()
    shutil.copytree(tmp_dataset.root / "schemas/1.1.0", tmp_dataset.root / "schemas/1.2.0")
    registries = dict(SCHEMA_REGISTRIES)
    registries["1.2.0"] = SCHEMA_REGISTRIES["1.1.0"]
    monkeypatch.setattr(evaluation_loaders, "SCHEMA_REGISTRIES", registries)

    policy_path = tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json"
    policy = tmp_dataset.read(policy_path)
    policy["artifact_schema_set_ref"]["reference"].update(
        version="1.2.0",
        hash=tmp_dataset._schema_set_hash("1.1.0"),
    )
    members = [
        policy["evaluation_profile_ref"],
        policy["comparison_policy_ref"],
        *policy["required_partition_refs"],
        *policy["required_gate_refs"],
        *policy["required_suite_refs"],
        policy["artifact_schema_set_ref"],
    ]
    policy["member_manifest_hash"] = canonical_sha256({"members": members})
    tmp_dataset.refresh_self_hash(policy)
    tmp_dataset.write(policy_path, policy)

    loaded = load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)

    assert loaded.manifest.schema_version == "1.1.0"
    assert any(
        reference.kind == "ARTIFACT_SCHEMA_SET" and reference.version == "1.2.0" for reference in loaded.reference_graph
    )


@pytest.mark.parametrize(
    ("outcome", "reason", "runtime_updates"),
    [
        ("NO_MATCH", None, {}),
        ("NOT_INVOKED", "BUNDLE_INELIGIBLE", {"bundle_eligibility_status": "SCOPE_INELIGIBLE"}),
    ],
)
def test_loader_accepts_v1_1_rule_outcomes_without_fake_rule_ids(
    tmp_dataset: MutableDatasetFixture,
    outcome: str,
    reason: str | None,
    runtime_updates: dict[str, str],
) -> None:
    tmp_dataset.upgrade_to_v1_1()

    def set_rule_outcome(case: dict[str, Any]) -> None:
        case["expected"].update(
            expected_rule_outcome=outcome,
            expected_rule_ids=[],
            expected_rule_not_invoked_reason=reason,
        )
        if outcome == "NOT_INVOKED":
            case["expected"].update(
                expected_provider_invocation=False,
                expected_retrieval_invocation=False,
            )
        case["context"]["runtime_fixture"].update(runtime_updates)

    tmp_dataset.mutate_case("rag-dev-safety-001", set_rule_outcome)

    loaded = load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)
    safety = next(case for case in loaded.cases if case.case_id == "rag-dev-safety-001")
    assert safety.expected.expected_rule_ids == ()


def test_loader_rejects_unknown_authoring_schema_version(tmp_dataset: MutableDatasetFixture) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["schema_version"] = "2.0.0"
    tmp_dataset.write_manifest(manifest)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


@pytest.mark.parametrize("reviewed_child", ["case", "evidence_mapping", "critical_claim_rubric"])
def test_frozen_v1_1_dataset_requires_every_gold_dependency_to_be_team_approved(
    tmp_dataset: MutableDatasetFixture,
    reviewed_child: str,
) -> None:
    tmp_dataset.upgrade_to_v1_1(frozen=True, reviewed_child=reviewed_child)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)


def test_frozen_v1_1_dataset_accepts_complete_team_approval_closure(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.upgrade_to_v1_1(frozen=True)

    loaded = load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)

    assert loaded.manifest.status.value == "FROZEN"


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


def test_loader_rejects_symlink_evals_root(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    evals_root.symlink_to(SOURCE_EVALS, target_is_directory=True)
    manifest = evals_root / SOURCE_MANIFEST.relative_to(SOURCE_EVALS)

    with pytest.raises(EvaluationValidationError) as caught:
        load_dataset(manifest, evals_root=evals_root)

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_loader_rejects_evals_root_below_symlink_ancestor(tmp_path: Path) -> None:
    linked_parent = tmp_path / "linked-repository"
    linked_parent.symlink_to(SOURCE_EVALS.parent, target_is_directory=True)
    evals_root = linked_parent / "evals"
    manifest = evals_root / SOURCE_MANIFEST.relative_to(SOURCE_EVALS)

    with pytest.raises(EvaluationValidationError) as caught:
        load_dataset(manifest, evals_root=evals_root)

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


@pytest.mark.parametrize("collection", ["classification_rules", "reason_code_catalog"])
def test_loader_rejects_duplicate_rubric_logical_ids_after_hash_rebinding(
    tmp_dataset: MutableDatasetFixture,
    collection: str,
) -> None:
    def duplicate_logical_id(rubric: dict[str, Any]) -> None:
        duplicate = dict(rubric[collection][0])
        duplicate["member_order"] = 2
        rubric[collection].append(duplicate)

    tmp_dataset.mutate_resource("critical_claim_rubric", duplicate_logical_id)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


@pytest.mark.parametrize(
    ("claim_id", "evidence_ref_id"),
    [
        ("SYNTHETIC_CLAIM_MISSING", "ev-synthetic-chunk-001"),
        ("SYNTHETIC_CLAIM_ANSWER_GROUNDING", "ev-synthetic-guideline-001"),
    ],
)
def test_loader_rejects_citation_outside_claim_support_after_hash_rebinding(
    tmp_dataset: MutableDatasetFixture,
    claim_id: str,
    evidence_ref_id: str,
) -> None:
    def mutate(case: dict[str, Any]) -> None:
        citation = case["expected"]["expected_citations"][0]
        citation["claim_id"] = claim_id
        citation["evidence_ref_id"] = evidence_ref_id

    tmp_dataset.mutate_case("rag-dev-answer-grounding-001", mutate)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_citation_locator_outside_mapped_evidence(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-answer-grounding-001",
        lambda case: case["expected"]["expected_citations"][0].__setitem__(
            "locator",
            "$.SYNTHETIC_MISSING",
        ),
    )

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def test_loader_rejects_expected_rule_with_non_rule_evidence_type(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-end-to-end-001",
        lambda case: case["expected"].__setitem__(
            "expected_rule_ids",
            ["ev-synthetic-guideline-001"],
        ),
    )

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def test_loader_rejects_forbidden_claim_reason_outside_rubric_catalog(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-end-to-end-001",
        lambda case: case["expected"]["forbidden_claims"][0].__setitem__(
            "reason_code",
            "SYNTHETIC_REASON_MISSING",
        ),
    )

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.RUBRIC_MISMATCH)


def test_loader_rejects_expected_scope_outside_rubric_scope(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.mutate_case(
        "rag-dev-answer-grounding-001",
        lambda case: case["expected"].__setitem__(
            "expected_scope_codes",
            ["SYNTHETIC_SCOPE_MISSING"],
        ),
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


@pytest.mark.parametrize("collision_key", ["context", "scopes", "runtime_fixture"])
def test_loader_redacts_every_flexible_ci_parameter_key_even_when_it_matches_schema_field(
    tmp_dataset: MutableDatasetFixture,
    collision_key: str,
) -> None:
    injected_value = "collision-value@example.com"
    tmp_dataset.mutate_config(
        "policies/dev-foundation-v1.comparison-policy.json",
        lambda value: value["scopes"][0].__setitem__("ci_parameters", {collision_key: injected_value}),
    )

    with pytest.raises(EvaluationValidationError) as caught:
        load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)

    assert caught.value.code is EvaluationErrorCode.PRIVACY_VALUE_DETECTED
    assert caught.value.safe_path == "/scopes/0/ci_parameters/*"
    message = str(caught.value)
    assert collision_key not in message.rpartition("/ci_parameters/")[2]
    assert injected_value not in message


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


def test_loader_rejects_schema_ids_swapped_between_registered_paths(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    first = tmp_dataset.root / "schemas/1.0.0/authoring/rag-eval.case.schema.json"
    second = tmp_dataset.root / "schemas/1.0.0/authoring/rag-eval.dataset-manifest.schema.json"
    temporary = tmp_dataset.root / "schemas/1.0.0/authoring/SYNTHETIC_SWAP.schema.json"
    first.rename(temporary)
    second.rename(first)
    temporary.rename(second)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_schema_set_with_unexpected_file(tmp_dataset: MutableDatasetFixture) -> None:
    stale = tmp_dataset.root / "schemas/1.0.0/stale.schema.json"
    stale.write_bytes(canonical_json_bytes({"$id": "urn:ah05:rag-eval:schema:stale:1.0.0"}))

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_schema_set_with_missing_file(tmp_dataset: MutableDatasetFixture) -> None:
    (tmp_dataset.root / "schemas/1.0.0/artifacts/rag-eval.run.schema.json").unlink()

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_duplicate_schema_identity_at_registered_path(tmp_dataset: MutableDatasetFixture) -> None:
    run_path = tmp_dataset.root / "schemas/1.0.0/artifacts/rag-eval.run.schema.json"
    gate_path = tmp_dataset.root / "schemas/1.0.0/artifacts/rag-eval.gate.schema.json"
    run_schema = tmp_dataset.read(run_path)
    gate_schema = tmp_dataset.read(gate_path)
    gate_schema["$id"] = run_schema["$id"]
    tmp_dataset.write(gate_path, gate_schema)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_profile_partition_set_that_disagrees_with_suite_and_policy(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    tmp_dataset.mutate_config(
        "profiles/dev-foundation-v1.profile.json",
        lambda value: value.update(required_partitions=["HOLDOUT"]),
    )
    tmp_dataset.rebind_configuration_refs()

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_comparison_scope_outside_bound_profile_and_suite(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    comparison_path = tmp_dataset.root / "policies/dev-foundation-v1.comparison-policy.json"
    comparison = tmp_dataset.read(comparison_path)
    comparison["scopes"][0]["partition"] = "HOLDOUT"
    tmp_dataset.refresh_self_hash(comparison)
    tmp_dataset.write(comparison_path, comparison)
    policy_path = tmp_dataset.root / "policies/dev-foundation-v1.evaluation-policy.json"
    policy = tmp_dataset.read(policy_path)
    policy["comparison_policy_ref"]["reference"]["hash"] = comparison["comparison_policy_hash"]
    members = [
        policy["evaluation_profile_ref"],
        policy["comparison_policy_ref"],
        *policy["required_partition_refs"],
        *policy["required_gate_refs"],
        *policy["required_suite_refs"],
        policy["artifact_schema_set_ref"],
    ]
    policy["member_manifest_hash"] = canonical_sha256({"members": members})
    tmp_dataset.refresh_self_hash(policy)
    tmp_dataset.write(policy_path, policy)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_accepts_git_provenance_without_loading_protected_receipt(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    manifest = tmp_dataset.manifest_value()
    manifest["fixture_git_commit_sha"] = "a" * 40
    manifest["protected_artifact_receipt_ref"] = None
    tmp_dataset.write_manifest(manifest)

    loaded = load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)

    assert loaded.manifest.fixture_git_commit_sha == "a" * 40
    assert loaded.protected_artifact_receipt is None


def test_loader_explicitly_rejects_unsupported_runtime_evidence_reference(
    tmp_dataset: MutableDatasetFixture,
) -> None:
    def use_runtime_reference(value: dict[str, Any]) -> None:
        entry = value["entries"][0]
        entry["target_kind"] = "RUNTIME_TYPED_REF"
        entry["runtime_typed_ref"] = {
            "id": entry["stable_key"],
            "version": entry["source_version"],
            "hash": entry["content_sha256"],
        }
        entry["fixture_record_ref"] = None

    tmp_dataset.mutate_resource("evidence_mapping", use_runtime_reference)

    assert_dataset_error(tmp_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def test_loader_does_not_misclassify_unexpected_eio_as_missing(
    tmp_dataset: MutableDatasetFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_read(_path: Path) -> bytes:
        raise OSError(errno.EIO, "SENSITIVE_SENTINEL")

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    with pytest.raises(OSError) as caught:
        load_dataset(tmp_dataset.manifest, evals_root=tmp_dataset.root)

    assert caught.value.errno == errno.EIO
