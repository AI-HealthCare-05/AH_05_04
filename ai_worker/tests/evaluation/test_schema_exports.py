from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import jsonschema  # type: ignore[import-untyped]
import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.loaders import _schema_set_hash, _SnapshotReader
from ai_worker.tasks.evaluation.schema_exports import (
    normalize_schema_document,
    schema_documents,
    write_schema_documents,
)
from ai_worker.tasks.evaluation.schema_registry import SCHEMA_REGISTRIES, SCHEMA_REGISTRY
from ai_worker.tasks.evaluation.schemas.artifacts import RESULT_ARTIFACT_MODELS

REPOSITORY_ROOT = Path(__file__).parents[3]
EVALS_ROOT = REPOSITORY_ROOT / "evals"


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*.json"))}


def _assert_no_schema_metadata(value: object, *, parent_key: str | None = None) -> None:
    map_key_namespaces = {"$defs", "definitions", "properties", "patternProperties", "dependentSchemas"}
    if isinstance(value, list):
        for item in value:
            _assert_no_schema_metadata(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if parent_key not in map_key_namespaces:
            assert key not in {"title", "description"}
        _assert_no_schema_metadata(item, parent_key=key)


def test_schema_normalization_removes_only_non_contract_metadata_recursively() -> None:
    source: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:rag-eval:test:1.0.0",
        "title": "remove",
        "description": "remove",
        "type": "object",
        "$defs": {
            "Value": {
                "title": "remove nested",
                "description": "remove nested",
                "type": "string",
                "minLength": 1,
            }
        },
    }
    original = deepcopy(source)

    normalized = normalize_schema_document(source)

    assert source == original
    assert normalized == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:rag-eval:test:1.0.0",
        "type": "object",
        "$defs": {"Value": {"type": "string", "minLength": 1}},
    }


def test_schema_normalization_preserves_contract_fields_named_title_or_description() -> None:
    source: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"title": "metadata", "type": "string", "minLength": 1},
            "description": {"description": "metadata", "type": "string", "maxLength": 500},
        },
        "required": ["title", "description"],
        "additionalProperties": False,
    }

    normalized = normalize_schema_document(source)

    assert normalized["properties"] == {
        "title": {"type": "string", "minLength": 1},
        "description": {"type": "string", "maxLength": 500},
    }


def test_schema_documents_are_complete_strict_draft_2020_12_contracts() -> None:
    documents = schema_documents()

    assert len(documents) == 18
    assert len(RESULT_ARTIFACT_MODELS) == 8
    assert "operational/rag-eval.validation-receipt.schema.json" in documents
    assert "operational/rag-eval.protected-artifact-receipt.schema.json" in documents
    for relative_path, document in documents.items():
        assert relative_path.endswith(".schema.json")
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        logical_name = relative_path.rsplit("/", 1)[1].removesuffix(".schema.json").removeprefix("rag-eval.")
        assert document["$id"] == f"urn:ah05:rag-eval:schema:{logical_name}:1.0.0"
        if "oneOf" in document and "properties" not in document:
            assert document["unevaluatedProperties"] is False
        else:
            assert document["additionalProperties"] is False
        _assert_no_schema_metadata(document)


def test_schema_registry_is_the_exact_unique_eighteen_file_contract() -> None:
    paths = [entry.relative_path for entry in SCHEMA_REGISTRY]
    schema_ids = [entry.schema_id for entry in SCHEMA_REGISTRY]

    assert len(paths) == len(set(paths)) == 18
    assert len(schema_ids) == len(set(schema_ids)) == 18
    assert set(paths) == set(schema_documents())


def test_schema_set_1_1_is_complete_and_preserves_member_versions() -> None:
    registry = SCHEMA_REGISTRIES["1.1.0"]
    documents = schema_documents("1.1.0")

    assert len(registry) == len({entry.relative_path for entry in registry}) == 18
    assert set(documents) == {entry.relative_path for entry in registry}
    versions = {entry.schema_id: entry.member_version for entry in registry}
    assert versions["rag-eval.case"] == "1.1.0"
    assert versions["rag-eval.dataset-manifest"] == "1.1.0"
    assert set(versions.values()) == {"1.0.0", "1.1.0"}


def test_schema_set_1_1_reuses_unchanged_members_byte_for_byte() -> None:
    version_1 = schema_documents()
    version_1_1 = schema_documents("1.1.0")
    changed = {
        "authoring/rag-eval.case.schema.json",
        "authoring/rag-eval.dataset-manifest.schema.json",
    }

    for path in set(version_1) - changed:
        assert canonical_json_bytes(version_1_1[path]) == canonical_json_bytes(version_1[path])


def test_schema_set_1_2_versions_exactly_the_provenance_bearing_members() -> None:
    registry = SCHEMA_REGISTRIES["1.2.0"]
    documents = schema_documents("1.2.0")
    versions = {entry.schema_id: entry.member_version for entry in registry}

    assert len(registry) == len({entry.relative_path for entry in registry}) == 18
    assert set(documents) == {entry.relative_path for entry in registry}
    assert {schema_id for schema_id, version in versions.items() if version == "1.2.0"} == {
        "rag-eval.case",
        "rag-eval.dataset-manifest",
        "rag-eval.evidence-mapping-manifest",
        "rag-eval.critical-claim-rubric",
        "rag-eval.evaluation-profile",
        "rag-eval.suite-definition",
        "rag-eval.evaluation-policy",
        "rag-eval.protected-artifact-receipt",
    }


def test_schema_set_1_3_replaces_only_dataset_manifest_and_adds_provenance_members() -> None:
    registry_v1_2 = SCHEMA_REGISTRIES["1.2.0"]
    registry_v1_3 = SCHEMA_REGISTRIES["1.3.0"]
    entries_v1_2 = {entry.relative_path: entry for entry in registry_v1_2}
    entries_v1_3 = {entry.relative_path: entry for entry in registry_v1_3}
    new_members = {
        "authoring/rag-eval.authoring-identity-manifest.schema.json": (
            "rag-eval.authoring-identity-manifest",
            "1.0.0",
        ),
        "operational/rag-eval.index-build-receipt.schema.json": (
            "rag-eval.index-build-receipt",
            "1.0.0",
        ),
        "operational/rag-eval.study-split-receipt.schema.json": (
            "rag-eval.study-split-receipt",
            "1.0.0",
        ),
    }

    assert len(registry_v1_3) == len(entries_v1_3) == 21
    assert len({entry.schema_id for entry in registry_v1_3}) == 21
    assert set(entries_v1_3) == set(entries_v1_2) | set(new_members)
    assert entries_v1_3["authoring/rag-eval.dataset-manifest.schema.json"].member_version == "1.3.0"
    assert {
        path: (entry.schema_id, entry.member_version) for path, entry in entries_v1_3.items() if path in new_members
    } == new_members
    for path in set(entries_v1_2) - {"authoring/rag-eval.dataset-manifest.schema.json"}:
        assert entries_v1_3[path] == entries_v1_2[path]


def test_schema_set_1_3_reuses_unchanged_1_2_members_byte_for_byte() -> None:
    version_1_2 = schema_documents("1.2.0")
    version_1_3 = schema_documents("1.3.0")

    for path in set(version_1_2) - {"authoring/rag-eval.dataset-manifest.schema.json"}:
        assert canonical_json_bytes(version_1_3[path]) == canonical_json_bytes(version_1_2[path])


def test_schema_set_1_4_adds_only_grounding_projection_members() -> None:
    registry_v1_3 = SCHEMA_REGISTRIES["1.3.0"]
    registry_v1_4 = SCHEMA_REGISTRIES["1.4.0"]
    entries_v1_3 = {entry.relative_path: entry for entry in registry_v1_3}
    entries_v1_4 = {entry.relative_path: entry for entry in registry_v1_4}
    new_members = {
        "artifacts/rag-eval.claim-citation-observation.schema.json": (
            "rag-eval.claim-citation-observation",
            "1.0.0",
        ),
        "artifacts/rag-eval.grounding-signal.schema.json": (
            "rag-eval.grounding-signal",
            "1.0.0",
        ),
    }

    assert len(registry_v1_4) == len(entries_v1_4) == 23
    assert len({entry.schema_id for entry in registry_v1_4}) == 23
    assert set(entries_v1_4) == set(entries_v1_3) | set(new_members)
    assert {
        path: (entry.schema_id, entry.member_version) for path, entry in entries_v1_4.items() if path in new_members
    } == new_members
    for path in entries_v1_3:
        assert entries_v1_4[path] == entries_v1_3[path]


def test_schema_set_1_4_reuses_every_1_3_member_byte_for_byte() -> None:
    version_1_3 = schema_documents("1.3.0")
    version_1_4 = schema_documents("1.4.0")

    for path, document in version_1_3.items():
        assert canonical_json_bytes(version_1_4[path]) == canonical_json_bytes(document)


def test_schema_set_1_4_exports_observation_state_conditions() -> None:
    document = cast(
        dict[str, Any],
        schema_documents("1.4.0")["artifacts/rag-eval.claim-citation-observation.schema.json"],
    )
    conditions = cast(list[dict[str, Any]], document["allOf"])
    decisions = {
        condition["if"]["properties"]["validation_decision"]["const"]
        for condition in conditions
        if "validation_decision" in condition.get("if", {}).get("properties", {})
    }
    authorization_states = {
        condition["if"]["properties"]["authorization_decision"].get("const", "NULL")
        for condition in conditions
        if "authorization_decision" in condition.get("if", {}).get("properties", {})
    }

    assert decisions == {"VALIDATED", "REJECTED"}
    assert authorization_states == {"AUTHORIZED", "REJECTED", "NULL"}
    assert "allOf" in document["$defs"]["CitationEdgeObservation"]
    assert "allOf" in document["$defs"]["ClaimObservation"]


def test_schema_set_1_4_exports_grounding_signal_state_conditions() -> None:
    document = cast(
        dict[str, Any],
        schema_documents("1.4.0")["artifacts/rag-eval.grounding-signal.schema.json"],
    )
    conditions = cast(list[dict[str, Any]], document["allOf"])

    assert {
        condition["if"]["properties"]["status"]["const"]
        for condition in conditions
        if "status" in condition.get("if", {}).get("properties", {})
    } == {"EVALUATED", "NOT_APPLICABLE_NO_CLAIMS"}


def _schema_set_1_4_observation_payload() -> dict[str, Any]:
    return {
        "schema_id": "rag-eval.claim-citation-observation",
        "schema_version": "1.0.0",
        "observation_sha256": "a" * 64,
        "run_id": "12345678-1234-4234-8234-123456789abc",
        "case_id": "case-001",
        "task_type": "ANSWER_GROUNDING",
        "dataset_code": "dev-foundation-v1",
        "dataset_version": "1.0.0",
        "input_sha256": "a" * 64,
        "answer_sha256": "b" * 64,
        "answer_variant_manifest_hash": "c" * 64,
        "validation_execution_status": "EVALUATED",
        "validation_decision": "VALIDATED",
        "validation_reason_codes": [],
        "validated_selection_sha256": "a" * 64,
        "authorization_decision": "AUTHORIZED",
        "authorization_reason_codes": [],
        "authorization_receipt_ref": {"id": "authorization-receipt", "version": "1.0.0", "hash": "b" * 64},
        "authorization_receipt_sha256": "c" * 64,
        "claims": [
            {
                "claim_key": "claim-001",
                "claim_kind": "MEDICAL",
                "criticality": "CRITICAL",
                "criticality_source": "GOLD_EXACT_MATCH",
                "criticality_review_ref": None,
                "support_status": "SUPPORTED",
                "support_receipt_sha256": "a" * 64,
                "citations": [
                    {
                        "citation_key": "citation-001",
                        "claim_key": "claim-001",
                        "source_type": "KNOWLEDGE_CHUNK",
                        "evidence_ref_id": "evidence-001",
                        "source_version": "1.0.0",
                        "locator": "section 1",
                        "content_sha256": "b" * 64,
                        "accepted": True,
                        "validation_reason_code": None,
                        "authorized": True,
                        "authorization_reason_code": None,
                        "authorization_selection_sha256": "c" * 64,
                        "gold_source_matched": True,
                    }
                ],
            }
        ],
    }


def _schema_set_1_4_signal_payload() -> dict[str, Any]:
    return {
        "schema_id": "rag-eval.grounding-signal",
        "schema_version": "1.0.0",
        "signal_sha256": "a" * 64,
        "run_id": "12345678-1234-4234-8234-123456789abc",
        "case_id": "case-001",
        "task_type": "SAFETY",
        "dataset_code": "dev-foundation-v1",
        "dataset_version": "1.0.0",
        "input_sha256": "a" * 64,
        "answer_sha256": None,
        "status": "NOT_APPLICABLE_NO_CLAIMS",
        "observation_ref": None,
        "observation_sha256": None,
        "critical_unsupported_claim": False,
        "uncited_medical_claim": False,
        "source_binding_misuse": False,
    }


def test_schema_set_1_4_observation_state_matrix_is_portable() -> None:
    document = schema_documents("1.4.0")["artifacts/rag-eval.claim-citation-observation.schema.json"]
    validator = jsonschema.Draft202012Validator(document)
    valid = _schema_set_1_4_observation_payload()

    assert validator.is_valid(valid)
    for field in ("validated_selection_sha256", "authorization_receipt_ref", "authorization_receipt_sha256"):
        invalid = deepcopy(valid)
        invalid[field] = None
        assert not validator.is_valid(invalid)
    invalid = deepcopy(valid)
    invalid["claims"][0]["citations"][0]["authorization_selection_sha256"] = None
    assert not validator.is_valid(invalid)

    rejected_before_authorization = deepcopy(valid)
    rejected_before_authorization["validation_decision"] = "REJECTED"
    rejected_before_authorization["validation_reason_codes"] = ["CLAIM_NOT_SUPPORTED"]
    rejected_before_authorization["validated_selection_sha256"] = None
    rejected_before_authorization["authorization_decision"] = None
    rejected_before_authorization["authorization_reason_codes"] = []
    rejected_before_authorization["authorization_receipt_ref"] = None
    rejected_before_authorization["authorization_receipt_sha256"] = None
    rejected_citation = rejected_before_authorization["claims"][0]["citations"][0]
    rejected_citation["authorized"] = False
    rejected_citation["authorization_reason_code"] = None
    rejected_citation["authorization_selection_sha256"] = None
    assert validator.is_valid(rejected_before_authorization)

    fabricated_authorization = deepcopy(valid)
    fabricated_authorization["validation_decision"] = "REJECTED"
    fabricated_authorization["validation_reason_codes"] = ["CLAIM_NOT_SUPPORTED"]
    fabricated_authorization["validated_selection_sha256"] = None
    assert not validator.is_valid(fabricated_authorization)


def test_schema_set_1_4_grounding_signal_state_matrix_is_portable() -> None:
    document = schema_documents("1.4.0")["artifacts/rag-eval.grounding-signal.schema.json"]
    validator = jsonschema.Draft202012Validator(document)
    valid = _schema_set_1_4_signal_payload()

    assert validator.is_valid(valid)
    for field, value in (
        ("observation_sha256", "c" * 64),
        ("critical_unsupported_claim", True),
    ):
        invalid = deepcopy(valid)
        invalid[field] = value
        assert not validator.is_valid(invalid)


def test_schema_set_1_4_new_members_are_strict_body_free_contracts() -> None:
    documents = schema_documents("1.4.0")
    observation = documents["artifacts/rag-eval.claim-citation-observation.schema.json"]
    signal = documents["artifacts/rag-eval.grounding-signal.schema.json"]
    expected_required = {
        "rag-eval.claim-citation-observation": {
            "schema_id",
            "schema_version",
            "observation_sha256",
            "run_id",
            "case_id",
            "task_type",
            "dataset_code",
            "dataset_version",
            "input_sha256",
            "answer_sha256",
            "answer_variant_manifest_hash",
            "validation_execution_status",
            "validation_decision",
            "validation_reason_codes",
            "validated_selection_sha256",
            "authorization_decision",
            "authorization_reason_codes",
            "authorization_receipt_ref",
            "authorization_receipt_sha256",
            "claims",
        },
        "rag-eval.grounding-signal": {
            "schema_id",
            "schema_version",
            "signal_sha256",
            "run_id",
            "case_id",
            "task_type",
            "dataset_code",
            "dataset_version",
            "input_sha256",
            "answer_sha256",
            "status",
            "observation_ref",
            "observation_sha256",
            "critical_unsupported_claim",
            "uncited_medical_claim",
            "source_binding_misuse",
        },
    }

    for schema_id, document in (
        ("rag-eval.claim-citation-observation", observation),
        ("rag-eval.grounding-signal", signal),
    ):
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["$id"] == f"urn:ah05:rag-eval:schema:{schema_id.removeprefix('rag-eval.')}:1.0.0"
        assert document["additionalProperties"] is False
        assert set(cast(list[str], document["required"])) == expected_required[schema_id]

    encoded = repr({"observation": observation, "signal": signal}).casefold()
    for forbidden in (
        "'query'",
        "'question'",
        "'answer_text'",
        "'claim_text'",
        "'source_body'",
        "'provider_payload'",
        "'credential'",
        "'patient'",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("relative_path", "pattern"),
    [
        (
            "docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md",
            r"rag-eval\.schema-set@1\.1\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "docs/governance/decisions/2026-09-02-rag-evaluation-schema-set-1-1-freeze.md",
            r"Schema Set SHA-256 \| `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "evals/README.md",
            r"rag-eval\.schema-set@1\.1\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
    ],
)
def test_documented_schema_set_1_1_hash_matches_committed_schema_set(
    relative_path: str,
    pattern: str,
) -> None:
    documented = re.search(pattern, (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))

    assert documented is not None
    assert documented.group("hash") == _schema_set_hash(_SnapshotReader(EVALS_ROOT), "1.1.0")


def test_schema_set_1_1_exports_rule_cardinality_and_fixture_consistency_conditions() -> None:
    case_schema = cast(
        dict[str, Any],
        schema_documents("1.1.0")["authoring/rag-eval.case.schema.json"],
    )
    definitions = cast(dict[str, Any], case_schema["$defs"])

    expected = cast(dict[str, Any], definitions["SafetyExpectedV11"])
    assert len(expected["oneOf"]) == 3
    assert {
        branch["properties"]["expected_rule_outcome"]["const"]: branch["properties"]["expected_rule_ids"]
        for branch in expected["oneOf"]
    } == {
        "MATCHED_RULES": {"minItems": 1},
        "NO_MATCH": {"maxItems": 0},
        "NOT_INVOKED": {"maxItems": 0},
    }

    runtime = cast(dict[str, Any], definitions["RuntimeFixtureV11"])
    assert len(runtime["oneOf"]) == 2
    safety_case = cast(dict[str, Any], definitions["SafetyCaseV11"])
    assert len(safety_case["allOf"]) >= 6


def test_array_minimums_and_synthetic_tokens_use_draft_2020_keywords() -> None:
    documents = schema_documents()
    saw_minimum_array = False
    saw_synthetic_pattern = False

    def visit(value: object) -> None:
        nonlocal saw_minimum_array, saw_synthetic_pattern
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("type") == "array" and "minItems" in value:
            saw_minimum_array = True
        if value.get("type") == "array":
            assert "minLength" not in value
        if value.get("pattern") == "^SYNTHETIC_":
            saw_synthetic_pattern = True
        for item in value.values():
            visit(item)

    for document in documents.values():
        visit(document)
    assert saw_synthetic_pattern
    assert saw_minimum_array


def test_schema_export_fails_closed_when_output_contains_stale_json(tmp_path: Path) -> None:
    write_schema_documents(tmp_path)
    stale = tmp_path / "stale.schema.json"
    stale.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="stale schema"):
        write_schema_documents(tmp_path)


def test_union_schema_roots_do_not_forbid_all_branch_properties() -> None:
    documents = schema_documents()

    for relative_path in (
        "authoring/rag-eval.case.schema.json",
        "artifacts/rag-eval.case-result.schema.json",
    ):
        document: Any = documents[relative_path]
        assert "oneOf" in document
        assert "additionalProperties" not in document
        assert document["unevaluatedProperties"] is False
        referenced_defs = [str(branch["$ref"]).removeprefix("#/$defs/") for branch in document["oneOf"]]
        assert referenced_defs
        assert all(document["$defs"][name]["additionalProperties"] is False for name in referenced_defs)


def test_exported_schema_encodes_execution_decision_and_receipt_feasibility() -> None:
    documents = schema_documents()
    run_schema = documents["artifacts/rag-eval.run.schema.json"]
    receipt_schema = documents["operational/rag-eval.validation-receipt.schema.json"]

    assert run_schema["allOf"]
    assert receipt_schema["oneOf"] == [
        {
            "properties": {
                "execution_status": {"const": "COMPLETED"},
                "decision_status": {"const": "N/A"},
            },
            "required": ["execution_status", "decision_status"],
        },
        {
            "properties": {
                "execution_status": {"enum": ["INVALID", "ERROR"]},
                "decision_status": {"type": "null"},
            },
            "required": ["execution_status", "decision_status"],
        },
    ]


def test_exported_metric_schema_encodes_concrete_state_dependent_nullability() -> None:
    document = cast(dict[str, Any], schema_documents()["artifacts/rag-eval.metrics.schema.json"])
    metric_schema = cast(dict[str, Any], document["$defs"]["MetricResult"])
    conditions = cast(list[dict[str, Any]], metric_schema["allOf"])

    assert {
        "if": {
            "properties": {"execution_status": {"enum": ["NOT_IMPLEMENTED", "NOT_EVALUATED", "INVALID", "ERROR"]}},
            "required": ["execution_status"],
        },
        "then": {
            "properties": {
                field: {"type": "null"}
                for field in (
                    "sample_case_count",
                    "sample_independent_group_count",
                    "numerator",
                    "denominator",
                    "metric_value",
                    "ci_lower",
                    "ci_upper",
                    "reason_code",
                )
            }
        },
    } in conditions
    assert any(
        condition.get("if", {}).get("properties", {}).get("required") == {"const": True}
        and condition.get("then", {}).get("properties", {}).get("decision_status") == {"not": {"const": "N/A"}}
        for condition in conditions
    )
    assert any(
        condition.get("if", {}).get("properties", {}).get("decision_status") == {"const": "INCONCLUSIVE"}
        and condition["then"]["properties"]["reason_code"] == {"not": {"type": "null"}}
        for condition in conditions
    )


def test_exported_content_manifest_schema_contains_exact_filename_allowlist() -> None:
    content_schema = cast(
        dict[str, Any],
        schema_documents()["artifacts/rag-eval.content-manifest.schema.json"],
    )
    path_schema = cast(
        dict[str, Any],
        content_schema["$defs"]["ContentArtifact"]["properties"]["relative_path"],
    )

    assert set(path_schema["enum"]) == {
        "cases.jsonl",
        "metrics.json",
        "suite-results.json",
        "comparison.json",
        "gate.json",
        "failures.jsonl",
        "report.md",
    }
    assert "run.json" not in path_schema["enum"]
    assert "result-content-manifest.json" not in path_schema["enum"]


def test_exported_review_provenance_schema_encodes_team_and_external_approval_conditions() -> None:
    authoring_schema = cast(
        dict[str, Any],
        schema_documents()["authoring/rag-eval.dataset-manifest.schema.json"],
    )
    provenance_schema = cast(dict[str, Any], authoring_schema["$defs"]["ReviewProvenance"])
    conditions = cast(list[dict[str, Any]], provenance_schema["allOf"])

    assert {
        "if": {
            "properties": {"team_gold_status": {"const": "APPROVED"}},
            "required": ["team_gold_status"],
        },
        "then": {
            "properties": {
                "approved_by": {"not": {"type": "null"}},
                "approved_at": {"not": {"type": "null"}},
            }
        },
        "else": {"properties": {"approved_by": {"type": "null"}, "approved_at": {"type": "null"}}},
    } in conditions
    assert any(
        condition.get("if", {}).get("properties", {}).get("external_medical_review_status") == {"const": "APPROVED"}
        and condition["then"]["properties"]["external_medical_approval_receipt_ref"] == {"not": {"type": "null"}}
        for condition in conditions
    )


def test_exported_review_provenance_v12_schema_encodes_draft_and_reviewed_state_matrix() -> None:
    authoring_schema = cast(
        dict[str, Any],
        schema_documents("1.2.0")["authoring/rag-eval.dataset-manifest.schema.json"],
    )
    provenance_schema = cast(dict[str, Any], authoring_schema["$defs"]["ReviewProvenanceV12"])
    conditions = cast(list[dict[str, Any]], provenance_schema["allOf"])

    assert {
        "if": {
            "properties": {"team_gold_status": {"const": "DRAFT"}},
            "required": ["team_gold_status"],
        },
        "then": {
            "properties": {
                "reviewed_by": {"type": "null"},
                "reviewed_at": {"type": "null"},
                "approved_by": {"type": "null"},
                "approved_at": {"type": "null"},
                "evidence_review_refs": {"maxItems": 0},
            }
        },
    } in conditions
    assert any(
        condition.get("if", {}).get("properties", {}).get("team_gold_status") == {"const": "REVIEWED"}
        and condition["then"]["properties"]["reviewed_by"]
        == {
            "allOf": [
                {"not": {"type": "null"}},
                {
                    "type": "object",
                    "properties": {"role": {"const": "EVALUATION_REVIEWER"}},
                    "required": ["role"],
                },
            ]
        }
        and condition["then"]["properties"]["evidence_review_refs"] == {"minItems": 1}
        for condition in conditions
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "authoring/rag-eval.dataset-manifest.schema.json",
        "operational/rag-eval.index-build-receipt.schema.json",
        "operational/rag-eval.study-split-receipt.schema.json",
    ],
)
def test_schema_set_1_3_review_provenance_v12_state_matrix_is_portable(
    relative_path: str,
) -> None:
    document = cast(dict[str, Any], schema_documents("1.3.0")[relative_path])
    definitions = cast(dict[str, Any], document["$defs"])
    provenance_schema = cast(dict[str, Any], definitions["ReviewProvenanceV12"])
    conditions = cast(list[dict[str, Any]], provenance_schema["allOf"])
    state_conditions = [
        condition
        for condition in conditions
        if condition.get("if", {}).get("properties", {}).get("team_gold_status", {}).get("const")
        in {"DRAFT", "REVIEWED", "APPROVED"}
        and "else" not in condition
    ]

    assert [condition["if"]["properties"]["team_gold_status"]["const"] for condition in state_conditions] == [
        "DRAFT",
        "REVIEWED",
        "APPROVED",
    ]
    assert state_conditions[0] == {
        "if": {
            "properties": {"team_gold_status": {"const": "DRAFT"}},
            "required": ["team_gold_status"],
        },
        "then": {
            "properties": {
                "reviewed_by": {"type": "null"},
                "reviewed_at": {"type": "null"},
                "approved_by": {"type": "null"},
                "approved_at": {"type": "null"},
                "evidence_review_refs": {"maxItems": 0},
            }
        },
    }

    invalid_draft: dict[str, Any] = {
        "authored_by": {
            "namespace": "GITHUB_LOGIN",
            "actor_id": "ceohwj",
            "role": "EVALUATION_IMPLEMENTER",
        },
        "reviewed_by": {
            "namespace": "GITHUB_LOGIN",
            "actor_id": "evaluation-reviewer",
            "role": "EVALUATION_REVIEWER",
        },
        "approved_by": None,
        "authored_at": "2026-09-05T00:00:00.000000Z",
        "reviewed_at": "2026-09-05T01:00:00.000000Z",
        "approved_at": None,
        "team_gold_status": "DRAFT",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }
    portable_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions,
        "$ref": "#/$defs/ReviewProvenanceV12",
    }

    assert list(jsonschema.Draft202012Validator(portable_schema).iter_errors(invalid_draft))


@pytest.mark.parametrize(
    ("relative_path", "definition_name", "field_name"),
    [
        (
            "authoring/rag-eval.authoring-identity-manifest.schema.json",
            "AuthoringIdentityEntry",
            "member_order",
        ),
        (
            "operational/rag-eval.study-split-receipt.schema.json",
            "QuestionTemplateAxisSummary",
            "comparison_count",
        ),
    ],
)
def test_schema_set_1_3_positive_integers_match_the_canonical_safe_integer_boundary(
    relative_path: str,
    definition_name: str,
    field_name: str,
) -> None:
    document = cast(dict[str, Any], schema_documents("1.3.0")[relative_path])
    definitions = cast(dict[str, Any], document["$defs"])
    definition = cast(dict[str, Any], definitions[definition_name])
    properties = cast(dict[str, Any], definition["properties"])
    field_schema = cast(dict[str, Any], properties[field_name])
    assert field_schema["exclusiveMinimum"] == 0
    assert field_schema["maximum"] == (2**53) - 1

    validator = jsonschema.Draft202012Validator(field_schema)

    assert validator.is_valid((2**53) - 1)
    assert not validator.is_valid(2**53)


def test_schema_set_1_3_study_split_axis_cardinality_is_portable() -> None:
    document = cast(
        dict[str, Any],
        schema_documents("1.3.0")["operational/rag-eval.study-split-receipt.schema.json"],
    )
    properties = cast(dict[str, Any], document["properties"])
    axis_summaries = cast(dict[str, Any], properties["axis_summaries"])
    assert axis_summaries["minItems"] == 4
    assert axis_summaries["maxItems"] == 4
    assert "minLength" not in axis_summaries
    assert "maxLength" not in axis_summaries

    portable_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": document["$defs"],
        **axis_summaries,
    }
    four_axes = [
        {"axis": axis, "comparison_count": 1, "intersection_count": 0}
        for axis in ("question_template", "source_segment", "medication_family", "transform_origin")
    ]
    validator = jsonschema.Draft202012Validator(portable_schema)

    assert validator.is_valid(four_axes)
    assert not validator.is_valid([*four_axes, four_axes[0]])
    duplicated_axes = [*four_axes[:-1], four_axes[0]]
    reordered_axes = [four_axes[1], four_axes[0], *four_axes[2:]]
    assert not validator.is_valid(duplicated_axes)
    assert not validator.is_valid(reordered_axes)
    assert not validator.is_valid([{}, {}, {}, {}])
    for invalid_summary in (
        {"axis": "question_template", "intersection_count": 0},
        {"axis": "question_template", "comparison_count": 0, "intersection_count": 0},
        {"axis": "question_template", "comparison_count": 1, "intersection_count": 1},
        {"axis": "question_template", "comparison_count": 1, "intersection_count": 0, "extra": True},
    ):
        assert not validator.is_valid([invalid_summary, *four_axes[1:]])


def _containing_approval_role_condition(roles: list[str]) -> dict[str, Any]:
    return {
        "if": {
            "properties": {
                "review_provenance": {
                    "properties": {"team_gold_status": {"const": "APPROVED"}},
                    "required": ["team_gold_status"],
                }
            },
            "required": ["review_provenance"],
        },
        "then": {
            "properties": {
                "review_provenance": {
                    "properties": {
                        "approved_by": {
                            "type": "object",
                            "properties": {"role": {"enum": roles}},
                            "required": ["role"],
                        }
                    },
                    "required": ["approved_by"],
                }
            }
        },
    }


def test_exported_authoring_schemas_encode_containing_approval_role_allowlists() -> None:
    documents = schema_documents()
    dataset_schema = cast(
        dict[str, Any],
        documents["authoring/rag-eval.dataset-manifest.schema.json"],
    )
    case_schema = cast(dict[str, Any], documents["authoring/rag-eval.case.schema.json"])
    case_definitions = cast(dict[str, Any], case_schema["$defs"])

    assert _containing_approval_role_condition(["DATASET_CUSTODIAN"]) in dataset_schema["allOf"]
    safety_roles = ["PRODUCT_SAFETY_REVIEWER", "MEDICAL_REVIEWER"]
    for definition_name in ("SafetyCase", "EndToEndRagCase"):
        definition = cast(dict[str, Any], case_definitions[definition_name])
        assert _containing_approval_role_condition(safety_roles) in definition["allOf"]


def test_schema_normalization_removes_metadata_only_from_schema_locations() -> None:
    source: dict[str, Any] = {
        "title": "root metadata",
        "description": "root metadata",
        "type": "object",
        "properties": {
            "title": {"title": "field metadata", "type": "string"},
            "description": {"description": "field metadata", "type": "string"},
        },
        "$defs": {"title": {"title": "definition metadata", "type": "string"}},
    }

    normalized = normalize_schema_document(source)

    assert normalized == {
        "type": "object",
        "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
        "$defs": {"title": {"type": "string"}},
    }


def test_committed_schema_files_match_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    write_schema_documents(tmp_path)

    committed_root = Path("evals/schemas/1.0.0")
    assert _files(tmp_path) == _files(committed_root)
    assert all(content == canonical_json_bytes(schema_documents()[path]) for path, content in _files(tmp_path).items())


def test_committed_schema_set_1_1_matches_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    write_schema_documents(tmp_path, "1.1.0")

    committed_root = Path("evals/schemas/1.1.0")
    assert _files(tmp_path) == _files(committed_root)


def test_committed_schema_set_1_2_matches_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    write_schema_documents(tmp_path, "1.2.0")

    committed_root = Path("evals/schemas/1.2.0")
    assert _files(tmp_path) == _files(committed_root)


def test_committed_schema_set_1_3_matches_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    write_schema_documents(tmp_path, "1.3.0")

    committed_root = Path("evals/schemas/1.3.0")
    assert _files(tmp_path) == _files(committed_root)


def test_committed_schema_set_1_4_matches_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    write_schema_documents(tmp_path, "1.4.0")

    committed_root = Path("evals/schemas/1.4.0")
    assert len(_files(tmp_path)) == 23
    assert _files(tmp_path) == _files(committed_root)


@pytest.mark.parametrize(
    ("relative_path", "pattern"),
    [
        (
            "docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md",
            r"rag-eval\.schema-set@1\.2\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "docs/governance/decisions/2026-09-03-rag-evaluation-schema-set-1-2-freeze.md",
            r"Schema Set SHA-256 \| `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "evals/README.md",
            r"rag-eval\.schema-set@1\.2\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
    ],
)
def test_documented_schema_set_1_2_hash_matches_committed_schema_set(
    relative_path: str,
    pattern: str,
) -> None:
    documented = re.search(pattern, (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))

    assert documented is not None
    assert documented.group("hash") == _schema_set_hash(_SnapshotReader(EVALS_ROOT), "1.2.0")


@pytest.mark.parametrize(
    ("relative_path", "pattern"),
    [
        (
            "docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md",
            r"rag-eval\.schema-set@1\.3\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md",
            r"Schema Set SHA-256 \| `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "evals/README.md",
            r"rag-eval\.schema-set@1\.3\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
    ],
)
def test_documented_schema_set_1_3_hash_matches_committed_schema_set(
    relative_path: str,
    pattern: str,
) -> None:
    documented = re.search(pattern, (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))

    assert documented is not None
    assert documented.group("hash") == _schema_set_hash(_SnapshotReader(EVALS_ROOT), "1.3.0")


@pytest.mark.parametrize(
    ("relative_path", "pattern"),
    [
        (
            "docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md",
            r"rag-eval\.schema-set@1\.4\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "docs/governance/decisions/2026-09-15-rag-evaluation-schema-set-1-4-candidate.md",
            r"Schema Set SHA-256 \| `(?P<hash>[0-9a-f]{64})`",
        ),
        (
            "evals/README.md",
            r"rag-eval\.schema-set@1\.4\.0`, SHA-256 `(?P<hash>[0-9a-f]{64})`",
        ),
    ],
)
def test_documented_schema_set_1_4_hash_matches_committed_schema_set(
    relative_path: str,
    pattern: str,
) -> None:
    path = REPOSITORY_ROOT / relative_path
    documented = re.search(pattern, path.read_text(encoding="utf-8")) if path.exists() else None

    assert documented is not None
    assert documented.group("hash") == _schema_set_hash(_SnapshotReader(EVALS_ROOT), "1.4.0")
