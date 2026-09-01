from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from ai_worker.tasks.evaluation.schema_registry import SCHEMA_REGISTRY, SchemaRegistryEntry, SchemaSource

_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def normalize_schema_document(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    map_key_namespaces = {"$defs", "definitions", "properties", "patternProperties", "dependentSchemas"}

    def normalize(value: JsonValue, *, parent_key: str | None = None) -> JsonValue:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            normalized = {
                key: normalize(item, parent_key=key)
                for key, item in value.items()
                if parent_key in map_key_namespaces or key not in {"title", "description"}
            }
            if normalized.get("type") == "array" and "minLength" in normalized:
                normalized["minItems"] = normalized.pop("minLength")
            return normalized
        return value

    return cast(dict[str, JsonValue], normalize(document))


def _model_schema(model: SchemaSource) -> dict[str, JsonValue]:
    if isinstance(model, TypeAdapter):
        return cast(dict[str, JsonValue], model.json_schema(mode="validation"))
    return cast(dict[str, JsonValue], model.model_json_schema(mode="validation"))


def _execution_decision_condition(execution_field: str, decision_field: str) -> dict[str, JsonValue]:
    return {
        "if": {
            "properties": {execution_field: {"const": "COMPLETED"}},
            "required": [execution_field],
        },
        "then": {"properties": {decision_field: {"not": {"type": "null"}}}},
        "else": {"properties": {decision_field: {"type": "null"}}},
    }


def _add_execution_decision_conditions(value: JsonValue) -> None:
    if isinstance(value, list):
        for item in value:
            _add_execution_decision_conditions(item)
        return
    if not isinstance(value, dict):
        return
    properties = value.get("properties")
    if isinstance(properties, dict):
        pairs = (
            ("execution_status", "decision_status"),
            ("aggregate_execution_status", "aggregate_decision_status"),
        )
        for execution_field, decision_field in pairs:
            if execution_field in properties and decision_field in properties:
                all_of = value.setdefault("allOf", [])
                if isinstance(all_of, list):
                    all_of.append(_execution_decision_condition(execution_field, decision_field))
    for item in value.values():
        _add_execution_decision_conditions(item)


def _add_run_conditions(document: dict[str, JsonValue]) -> None:
    runtime_fields = [
        "candidate_bundle_id",
        "candidate_bundle_manifest_hash",
        "candidate_guard_decision_id",
        "candidate_guard_decision",
        "required_case_guard_coverage_manifest_hash",
    ]
    all_of = document.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("run schema allOf must be an array")
    all_of.extend(
        [
            {
                "if": {
                    "properties": {"execution_status": {"const": "COMPLETED"}},
                    "required": ["execution_status"],
                },
                "then": {
                    "properties": {
                        "completed_at": {"type": "string"},
                        "result_content_manifest_hash": {"type": "string"},
                    }
                },
                "else": {
                    "properties": {
                        "completed_at": {"type": "null"},
                        "result_content_manifest_hash": {"type": "null"},
                    }
                },
            },
            {
                "if": {
                    "properties": {"runtime_eligible": {"const": True}},
                    "required": ["runtime_eligible"],
                },
                "then": {
                    "properties": {
                        "experiment_type": {"const": "END_TO_END_RAG"},
                        "environment": {"const": "LOCAL"},
                        **{field: {"not": {"type": "null"}} for field in runtime_fields},
                    }
                },
                "else": {"properties": {field: {"type": "null"} for field in runtime_fields}},
            },
        ]
    )


def _add_receipt_outcomes(document: dict[str, JsonValue]) -> None:
    document["oneOf"] = [
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


def _add_metric_conditions(document: dict[str, JsonValue]) -> None:
    definitions = document.get("$defs")
    if not isinstance(definitions, dict):
        raise TypeError("metrics schema definitions must be an object")
    metric = definitions.get("MetricResult")
    if not isinstance(metric, dict):
        raise TypeError("MetricResult schema must be an object")
    all_of = metric.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("MetricResult allOf must be an array")

    calculated_fields = (
        "sample_case_count",
        "sample_independent_group_count",
        "numerator",
        "denominator",
        "metric_value",
        "ci_lower",
        "ci_upper",
        "reason_code",
    )
    completed_count_fields = (
        "sample_case_count",
        "sample_independent_group_count",
        "numerator",
        "denominator",
    )
    inconclusive_fields = (
        "sample_case_count",
        "sample_independent_group_count",
        "denominator",
        "reason_code",
    )
    all_of.extend(
        [
            {
                "if": {
                    "properties": {
                        "execution_status": {"enum": ["NOT_IMPLEMENTED", "NOT_EVALUATED", "INVALID", "ERROR"]}
                    },
                    "required": ["execution_status"],
                },
                "then": {"properties": {field: {"type": "null"} for field in calculated_fields}},
            },
            {
                "if": {"properties": {"required": {"const": True}}, "required": ["required"]},
                "then": {"properties": {"decision_status": {"not": {"const": "N/A"}}}},
            },
            {
                "if": {
                    "properties": {"execution_status": {"const": "COMPLETED"}},
                    "required": ["execution_status"],
                },
                "then": {"properties": {field: {"not": {"type": "null"}} for field in completed_count_fields}},
            },
            {
                "if": {
                    "properties": {"decision_status": {"const": "INCONCLUSIVE"}},
                    "required": ["decision_status"],
                },
                "then": {"properties": {field: {"not": {"type": "null"}} for field in inconclusive_fields}},
            },
        ]
    )


def _add_review_provenance_conditions(value: JsonValue) -> None:
    if isinstance(value, list):
        for item in value:
            _add_review_provenance_conditions(item)
        return
    if not isinstance(value, dict):
        return
    properties = value.get("properties")
    if isinstance(properties, dict) and {
        "team_gold_status",
        "approved_by",
        "approved_at",
        "external_medical_review_status",
        "external_medical_approval_receipt_ref",
    }.issubset(properties):
        all_of = value.setdefault("allOf", [])
        if not isinstance(all_of, list):
            raise TypeError("ReviewProvenance allOf must be an array")
        all_of.extend(
            [
                {
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
                    "else": {
                        "properties": {
                            "approved_by": {"type": "null"},
                            "approved_at": {"type": "null"},
                        }
                    },
                },
                {
                    "if": {
                        "properties": {"external_medical_review_status": {"const": "APPROVED"}},
                        "required": ["external_medical_review_status"],
                    },
                    "then": {"properties": {"external_medical_approval_receipt_ref": {"not": {"type": "null"}}}},
                    "else": {"properties": {"external_medical_approval_receipt_ref": {"type": "null"}}},
                },
            ]
        )
    for item in value.values():
        _add_review_provenance_conditions(item)


def _containing_approval_role_condition(roles: list[str]) -> dict[str, JsonValue]:
    role_values: list[JsonValue] = [role for role in roles]
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
                            "properties": {"role": {"enum": role_values}},
                            "required": ["role"],
                        }
                    },
                    "required": ["approved_by"],
                }
            }
        },
    }


def _append_containing_approval_roles(schema: dict[str, JsonValue], roles: list[str]) -> None:
    all_of = schema.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("containing authoring schema allOf must be an array")
    all_of.append(_containing_approval_role_condition(roles))


def _add_authoring_role_conditions(schema_id: str, document: dict[str, JsonValue]) -> None:
    if schema_id == "rag-eval.dataset-manifest":
        _append_containing_approval_roles(document, ["DATASET_CUSTODIAN"])
        return
    if schema_id != "rag-eval.case":
        return
    definitions = document.get("$defs")
    if not isinstance(definitions, dict):
        raise TypeError("case schema definitions must be an object")
    roles = ["PRODUCT_SAFETY_REVIEWER", "MEDICAL_REVIEWER"]
    for definition_name in ("SafetyCase", "EndToEndRagCase"):
        definition = definitions.get(definition_name)
        if not isinstance(definition, dict):
            raise TypeError(f"{definition_name} schema must be an object")
        _append_containing_approval_roles(definition, roles)


def _add_dataset_source_provenance_condition(document: dict[str, JsonValue]) -> None:
    all_of = document.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("dataset manifest schema allOf must be an array")
    all_of.append(
        {
            "oneOf": [
                {
                    "properties": {
                        "fixture_git_commit_sha": {"not": {"type": "null"}},
                        "protected_artifact_receipt_ref": {"type": "null"},
                    },
                    "required": ["fixture_git_commit_sha", "protected_artifact_receipt_ref"],
                },
                {
                    "properties": {
                        "fixture_git_commit_sha": {"type": "null"},
                        "protected_artifact_receipt_ref": {"not": {"type": "null"}},
                    },
                    "required": ["fixture_git_commit_sha", "protected_artifact_receipt_ref"],
                },
            ]
        }
    )


def _add_evidence_target_condition(document: dict[str, JsonValue]) -> None:
    definitions = document.get("$defs")
    if not isinstance(definitions, dict):
        raise TypeError("evidence mapping schema definitions must be an object")
    entry = definitions.get("EvidenceMappingEntry")
    if not isinstance(entry, dict):
        raise TypeError("EvidenceMappingEntry schema must be an object")
    all_of = entry.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("EvidenceMappingEntry allOf must be an array")
    all_of.append(
        {
            "oneOf": [
                {
                    "properties": {
                        "target_kind": {"const": "RUNTIME_TYPED_REF"},
                        "runtime_typed_ref": {"not": {"type": "null"}},
                        "fixture_record_ref": {"type": "null"},
                    },
                    "required": ["target_kind", "runtime_typed_ref", "fixture_record_ref"],
                },
                {
                    "properties": {
                        "target_kind": {"const": "FIXTURE_RECORD"},
                        "runtime_typed_ref": {"type": "null"},
                        "fixture_record_ref": {"not": {"type": "null"}},
                    },
                    "required": ["target_kind", "runtime_typed_ref", "fixture_record_ref"],
                },
            ]
        }
    )


def _schema_document(entry: SchemaRegistryEntry) -> dict[str, JsonValue]:
    schema_id = entry.schema_id
    document = _model_schema(entry.source)
    document["$schema"] = _DRAFT_2020_12
    document["$id"] = entry.urn
    if "oneOf" in document and "properties" not in document:
        document.pop("additionalProperties", None)
        document["unevaluatedProperties"] = False
    else:
        document["additionalProperties"] = False
    _add_execution_decision_conditions(document)
    _add_review_provenance_conditions(document)
    _add_authoring_role_conditions(schema_id, document)
    if schema_id == "rag-eval.dataset-manifest":
        _add_dataset_source_provenance_condition(document)
    if schema_id == "rag-eval.evidence-mapping-manifest":
        _add_evidence_target_condition(document)
    if schema_id == "rag-eval.run":
        _add_run_conditions(document)
    if schema_id == "rag-eval.validation-receipt":
        _add_receipt_outcomes(document)
    if schema_id == "rag-eval.metrics":
        _add_metric_conditions(document)
    return normalize_schema_document(document)


def schema_documents() -> dict[str, dict[str, JsonValue]]:
    return dict(sorted((entry.relative_path, _schema_document(entry)) for entry in SCHEMA_REGISTRY))


def write_schema_documents(root: Path) -> None:
    documents = schema_documents()
    existing = {path.relative_to(root).as_posix() for path in root.rglob("*.json")} if root.exists() else set()
    stale = existing - set(documents)
    if stale:
        raise ValueError("stale schema files present")
    for relative_path, document in documents.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_json_bytes(document))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export deterministic RAG evaluation JSON Schemas")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    write_schema_documents(args.output)


if __name__ == "__main__":
    main()
