from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.schema_exports import (
    normalize_schema_document,
    schema_documents,
    write_schema_documents,
)
from ai_worker.tasks.evaluation.schema_registry import SCHEMA_REGISTRY
from ai_worker.tasks.evaluation.schemas.artifacts import RESULT_ARTIFACT_MODELS


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
