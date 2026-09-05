from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.schema_exports import write_schema_documents
from ai_worker.tests.evaluation.test_loaders import SOURCE_EVALS, MutableDatasetFixture

AUTHORING_PATH = "retrieval/manifests/dev-foundation-v1.authoring-identities.json"


def _refresh_self_hash(value: dict[str, Any]) -> None:
    value["manifest_sha256"] = canonical_sha256(
        value,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )


def _authoring_payload(fixture: MutableDatasetFixture) -> dict[str, Any]:
    manifest = fixture.manifest_value()
    entries = []
    for order, resource in enumerate(manifest["case_resources"], start=1):
        case = fixture.read(fixture.root / resource["path"])
        leakage = case["leakage_group_ids"]
        entries.append(
            {
                "member_order": order,
                "case_id": case["case_id"],
                "question_template_id": leakage["question_template"],
                "source_segment_id": leakage["source_segment"],
                "medication_family_id": leakage["medication_family"],
                "transform_origin_id": leakage["transform_origin"],
                "question_template_spec": f"template specification {order}",
                "source_snapshot_ref": case["context"]["runtime_fixture"]["source_snapshot_ref"],
                "source_locator": f"synthetic-section-{order}",
                "source_chunk_sha256": "a" * 64,
                "medication_family_fixture_id": f"synthetic-medication-family-{order}",
                "base_intent_seed": f"synthetic-base-intent-{order}",
                "transform_spec": f"synthetic transform specification {order}",
            }
        )
    payload = {
        "schema_id": "rag-eval.authoring-identity-manifest",
        "schema_version": "1.0.0",
        "manifest_id": "dev-foundation-authoring-identities",
        "manifest_version": "1.0.0",
        "dataset_code": manifest["dataset_code"],
        "dataset_version": manifest["dataset_version"],
        "canonicalization_spec_version": "1.0.0",
        "entries": entries,
        "manifest_sha256": "0" * 64,
    }
    _refresh_self_hash(payload)
    return payload


def _write_authoring(
    fixture: MutableDatasetFixture,
    payload: dict[str, Any],
    *,
    refresh_self_hash: bool = True,
) -> Path:
    if refresh_self_hash:
        _refresh_self_hash(payload)
    path = fixture.root / AUTHORING_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    manifest = fixture.manifest_value()
    manifest["authoring_identity_manifest_ref"] = {
        "path": AUTHORING_PATH,
        "sha256": sha256_hex(path.read_bytes()),
    }
    fixture.write_manifest(manifest)
    return path


def _upgrade_to_v1_3(fixture: MutableDatasetFixture) -> dict[str, Any]:
    fixture.upgrade_to_v1_2()
    write_schema_documents(fixture.root / "schemas/1.3.0", "1.3.0")

    policy_path = fixture.root / "policies/dev-foundation-v1.evaluation-policy.json"
    policy = fixture.read(policy_path)
    policy["artifact_schema_set_ref"]["reference"].update(
        version="1.3.0",
        hash=fixture._schema_set_hash("1.3.0"),
    )
    policy["member_manifest_hash"] = canonical_sha256(
        {
            "members": [
                policy["evaluation_profile_ref"],
                policy["comparison_policy_ref"],
                *policy["required_partition_refs"],
                *policy["required_gate_refs"],
                *policy["required_suite_refs"],
                policy["artifact_schema_set_ref"],
            ]
        }
    )
    fixture.refresh_self_hash(policy)
    fixture.write(policy_path, policy)

    payload = _authoring_payload(fixture)
    path = fixture.root / AUTHORING_PATH
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    manifest = fixture.manifest_value()
    manifest.update(
        schema_version="1.3.0",
        authoring_identity_manifest_ref={
            "path": AUTHORING_PATH,
            "sha256": sha256_hex(path.read_bytes()),
        },
    )
    fixture.write_manifest(manifest)
    return payload


@pytest.fixture
def authoring_dataset(tmp_path: Path) -> tuple[MutableDatasetFixture, dict[str, Any]]:
    root = tmp_path / "evals"
    shutil.copytree(SOURCE_EVALS, root)
    fixture = MutableDatasetFixture(root)
    return fixture, _upgrade_to_v1_3(fixture)


def _assert_error(fixture: MutableDatasetFixture, code: EvaluationErrorCode) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        load_dataset(fixture.manifest, evals_root=fixture.root)
    assert caught.value.code is code


def test_loader_loads_v1_3_authoring_identity_as_graph_resource(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
) -> None:
    fixture, payload = authoring_dataset

    loaded = load_dataset(fixture.manifest, evals_root=fixture.root)

    assert loaded.manifest.schema_version == "1.3.0"
    assert loaded.authoring_identity_manifest is not None
    assert loaded.authoring_identity_manifest.manifest_id == payload["manifest_id"]
    assert (
        dict(loaded.resource_hashes)[AUTHORING_PATH]
        == fixture.manifest_value()["authoring_identity_manifest_ref"]["sha256"]
    )
    assert (
        "AUTHORING_IDENTITY_MANIFEST",
        payload["manifest_id"],
        payload["manifest_version"],
        fixture.manifest_value()["authoring_identity_manifest_ref"]["sha256"],
    ) in {(item.kind, item.id, item.version, item.hash) for item in loaded.reference_graph}


def test_loader_requires_fixed_authoring_identity_sidecar_path(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
) -> None:
    fixture, _ = authoring_dataset
    source = fixture.root / AUTHORING_PATH
    alternate_relative = "retrieval/manifests/alternate.authoring-identities.json"
    alternate = fixture.root / alternate_relative
    alternate.write_bytes(source.read_bytes())
    manifest = fixture.manifest_value()
    manifest["authoring_identity_manifest_ref"] = {
        "path": alternate_relative,
        "sha256": sha256_hex(alternate.read_bytes()),
    }
    fixture.write_manifest(manifest)

    _assert_error(fixture, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_missing_authoring_identity_sidecar(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
) -> None:
    fixture, _ = authoring_dataset
    (fixture.root / AUTHORING_PATH).unlink()

    _assert_error(fixture, EvaluationErrorCode.RESOURCE_MISSING)


def test_loader_rejects_authoring_identity_raw_reference_hash_mismatch(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
) -> None:
    fixture, _ = authoring_dataset
    manifest = fixture.manifest_value()
    manifest["authoring_identity_manifest_ref"]["sha256"] = "f" * 64
    fixture.write_manifest(manifest)

    _assert_error(fixture, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_authoring_identity_self_hash_mismatch(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
) -> None:
    fixture, payload = authoring_dataset
    payload["canonicalization_spec_version"] = "1.1.0"
    _write_authoring(fixture, payload, refresh_self_hash=False)

    _assert_error(fixture, EvaluationErrorCode.HASH_MISMATCH)


@pytest.mark.parametrize("field", ["dataset_code", "dataset_version"])
def test_loader_rejects_authoring_identity_dataset_mismatch(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
    field: str,
) -> None:
    fixture, payload = authoring_dataset
    payload[field] = "synthetic-mismatch" if field == "dataset_code" else "9.9.9"
    _write_authoring(fixture, payload)

    _assert_error(fixture, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "reordered"])
def test_loader_rejects_non_exact_authoring_identity_case_members(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
    mutation: str,
) -> None:
    fixture, payload = authoring_dataset
    entries = payload["entries"]
    if mutation == "missing":
        entries.pop()
    elif mutation == "extra":
        extra = deepcopy(entries[-1])
        extra.update(member_order=len(entries) + 1, case_id="synthetic-extra-case")
        entries.append(extra)
    elif mutation == "duplicate":
        duplicate = deepcopy(entries[-1])
        duplicate["member_order"] = len(entries) + 1
        entries.append(duplicate)
    else:
        entries.reverse()
        for order, entry in enumerate(entries, start=1):
            entry["member_order"] = order
    _write_authoring(fixture, payload)

    _assert_error(fixture, EvaluationErrorCode.MANIFEST_INVALID)


@pytest.mark.parametrize(
    ("sidecar_field", "case_field"),
    [
        ("question_template_id", "question_template"),
        ("source_segment_id", "source_segment"),
        ("medication_family_id", "medication_family"),
        ("transform_origin_id", "transform_origin"),
    ],
)
def test_loader_rejects_each_authoring_identity_leakage_mismatch(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
    sidecar_field: str,
    case_field: str,
) -> None:
    fixture, payload = authoring_dataset
    payload["entries"][0][sidecar_field] = f"synthetic-mismatched-{case_field}"
    _write_authoring(fixture, payload)

    _assert_error(fixture, EvaluationErrorCode.MANIFEST_INVALID)


def test_loader_rejects_authoring_identity_member_schema_version_mismatch(
    authoring_dataset: tuple[MutableDatasetFixture, dict[str, Any]],
) -> None:
    fixture, payload = authoring_dataset
    payload["schema_version"] = "9.9.9"
    _write_authoring(fixture, payload)

    _assert_error(fixture, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_keeps_v1_2_authoring_identity_field_none(tmp_path: Path) -> None:
    root = tmp_path / "evals"
    shutil.copytree(SOURCE_EVALS, root)
    fixture = MutableDatasetFixture(root)
    fixture.upgrade_to_v1_2()

    loaded = load_dataset(fixture.manifest, evals_root=fixture.root)

    assert loaded.manifest.schema_version == "1.2.0"
    assert loaded.authoring_identity_manifest is None
