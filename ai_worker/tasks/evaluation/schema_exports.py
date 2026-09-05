from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from ai_worker.tasks.evaluation.schema_registry import (
    SCHEMA_REGISTRIES,
    SCHEMA_VERSION,
    SchemaRegistryEntry,
    SchemaSource,
)

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
            {
                "if": {
                    "allOf": [
                        {
                            "properties": {"required": {"const": True}},
                            "required": ["required"],
                        },
                        {
                            "anyOf": [
                                {
                                    "properties": {field: {"const": 0}},
                                    "required": [field],
                                }
                                for field in (
                                    "sample_case_count",
                                    "sample_independent_group_count",
                                    "denominator",
                                )
                            ]
                        },
                    ]
                },
                "then": {"properties": {"decision_status": {"const": "INCONCLUSIVE"}}},
            },
        ]
    )


def _add_empty_aggregate_conditions(schema_id: str, document: dict[str, JsonValue]) -> None:
    if schema_id == "rag-eval.suite-results":
        empty_condition: dict[str, JsonValue] = {
            "properties": {"case_results": {"maxItems": 0}},
            "required": ["case_results"],
        }
    elif schema_id == "rag-eval.gate":
        required_groups = ("required_metrics", "required_suites", "required_contract_receipts")
        empty_condition = {
            "properties": {field: {"maxItems": 0} for field in required_groups},
            "required": list(required_groups),
        }
    else:
        return

    all_of = document.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("aggregate schema allOf must be an array")
    all_of.append(
        {
            "if": empty_condition,
            "then": {
                "properties": {
                    "aggregate_execution_status": {"not": {"const": "COMPLETED"}},
                    "aggregate_decision_status": {"type": "null"},
                    "blocking_execution_statuses": {"const": []},
                }
            },
        }
    )


def _array_contains_schema(field: str, item_schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "properties": {
            field: {
                "contains": item_schema,
            }
        },
        "required": [field],
    }


def _array_does_not_contain_schema(field: str, item_schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "properties": {
            field: {
                "not": {
                    "contains": item_schema,
                }
            }
        },
        "required": [field],
    }


def _nested_value_schema(nested_field: str, value: JsonValue) -> dict[str, JsonValue]:
    return {
        "properties": {nested_field: {"const": value}},
        "required": [nested_field],
    }


def _array_contains(field: str, nested_field: str, value: JsonValue) -> dict[str, JsonValue]:
    return _array_contains_schema(field, _nested_value_schema(nested_field, value))


def _array_does_not_contain(field: str, nested_field: str, value: JsonValue) -> dict[str, JsonValue]:
    return _array_does_not_contain_schema(field, _nested_value_schema(nested_field, value))


def _array_is_empty(field: str) -> dict[str, JsonValue]:
    return {
        "properties": {field: {"maxItems": 0}},
        "required": [field],
    }


def _aggregate_sources_contain(
    fields: tuple[str, ...],
    nested_field: str,
    value: JsonValue,
) -> dict[str, JsonValue]:
    return {"anyOf": [_array_contains(field, nested_field, value) for field in fields]}


def _aggregate_sources_do_not_contain(
    fields: tuple[str, ...],
    nested_field: str,
    value: JsonValue,
) -> dict[str, JsonValue]:
    return {"allOf": [_array_does_not_contain(field, nested_field, value) for field in fields]}


def _aggregate_sources_nonempty(fields: tuple[str, ...]) -> dict[str, JsonValue]:
    return {
        "anyOf": [
            {
                "properties": {field: {"minItems": 1}},
                "required": [field],
            }
            for field in fields
        ]
    }


_BLOCKING_EXECUTION_PRECEDENCE = ("INVALID", "ERROR", "NOT_IMPLEMENTED", "NOT_EVALUATED")


def _aggregate_blocker_condition(
    fields: tuple[str, ...],
    blocking_statuses: tuple[str, ...],
) -> dict[str, JsonValue]:
    conditions: list[JsonValue] = [_aggregate_sources_nonempty(fields)]
    for status in _BLOCKING_EXECUTION_PRECEDENCE:
        condition = (
            _aggregate_sources_contain(fields, "execution_status", status)
            if status in blocking_statuses
            else _aggregate_sources_do_not_contain(fields, "execution_status", status)
        )
        conditions.append(condition)
    return {"allOf": conditions}


def _add_aggregate_outcome_conditions(schema_id: str, document: dict[str, JsonValue]) -> None:
    fields: tuple[str, ...]
    if schema_id == "rag-eval.suite-results":
        fields = ("case_results",)
    elif schema_id == "rag-eval.gate":
        fields = ("required_metrics", "required_suites", "required_contract_receipts")
    else:
        return

    all_of = document.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("aggregate schema allOf must be an array")

    for mask in range(1 << len(_BLOCKING_EXECUTION_PRECEDENCE)):
        blocking_statuses = tuple(
            status for index, status in enumerate(_BLOCKING_EXECUTION_PRECEDENCE) if mask & (1 << index)
        )
        aggregate_properties: dict[str, JsonValue] = {
            "blocking_execution_statuses": {"const": list(blocking_statuses)},
        }
        if blocking_statuses:
            aggregate_properties.update(
                aggregate_execution_status={"const": blocking_statuses[0]},
                aggregate_decision_status={"type": "null"},
            )
        else:
            aggregate_properties["aggregate_execution_status"] = {"const": "COMPLETED"}
        all_of.append(
            {
                "if": _aggregate_blocker_condition(fields, blocking_statuses),
                "then": {"properties": aggregate_properties},
            }
        )

    all_completed = _aggregate_blocker_condition(fields, ())
    no_fail = _aggregate_sources_do_not_contain(fields, "decision_status", "FAIL")
    no_inconclusive = _aggregate_sources_do_not_contain(fields, "decision_status", "INCONCLUSIVE")
    no_pass = _aggregate_sources_do_not_contain(fields, "decision_status", "PASS")
    all_of.extend(
        [
            {
                "if": {"allOf": [all_completed, _aggregate_sources_contain(fields, "decision_status", "FAIL")]},
                "then": {"properties": {"aggregate_decision_status": {"const": "FAIL"}}},
            },
            {
                "if": {
                    "allOf": [
                        all_completed,
                        no_fail,
                        _aggregate_sources_contain(fields, "decision_status", "INCONCLUSIVE"),
                    ]
                },
                "then": {"properties": {"aggregate_decision_status": {"const": "INCONCLUSIVE"}}},
            },
            {
                "if": {
                    "allOf": [
                        all_completed,
                        no_fail,
                        no_inconclusive,
                        _aggregate_sources_contain(fields, "decision_status", "PASS"),
                    ]
                },
                "then": {"properties": {"aggregate_decision_status": {"const": "PASS"}}},
            },
            {
                "if": {"allOf": [all_completed, no_fail, no_inconclusive, no_pass]},
                "then": {"properties": {"aggregate_decision_status": {"const": "N/A"}}},
            },
        ]
    )

    if schema_id == "rag-eval.suite-results":
        all_of.append(
            {
                "if": {"properties": {"required": {"const": True}}, "required": ["required"]},
                "then": {"properties": {"aggregate_decision_status": {"not": {"const": "N/A"}}}},
            }
        )


def _add_gate_member_conditions(document: dict[str, JsonValue]) -> None:
    definitions = document.get("$defs")
    if not isinstance(definitions, dict):
        raise TypeError("gate schema definitions must be an object")
    member = definitions.get("RequiredGateMember")
    if not isinstance(member, dict):
        raise TypeError("RequiredGateMember schema must be an object")
    all_of = member.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("RequiredGateMember allOf must be an array")
    all_of.append({"properties": {"decision_status": {"not": {"const": "N/A"}}}})

    properties = document.get("properties")
    if not isinstance(properties, dict):
        raise TypeError("gate schema properties must be an object")
    member_types = {
        "required_metrics": "METRIC",
        "required_suites": "SUITE",
        "required_contract_receipts": "CONTRACT_RECEIPT",
    }
    for field, member_type in member_types.items():
        collection = properties.get(field)
        if not isinstance(collection, dict):
            raise TypeError("gate member collection schema must be an object")
        item_schema = collection.get("items")
        if not isinstance(item_schema, dict):
            raise TypeError("gate member item schema must be an object")
        collection["items"] = {
            "allOf": [
                item_schema,
                {
                    "properties": {"member_type": {"const": member_type}},
                    "required": ["member_type"],
                },
            ]
        }


def _add_comparison_outcome_conditions(document: dict[str, JsonValue]) -> None:
    mismatch = _array_contains("controlled_variable_checks", "matched", False)
    invalid_inputs: dict[str, JsonValue] = {
        "anyOf": [
            mismatch,
            _array_is_empty("controlled_variable_checks"),
            _array_is_empty("scope_comparisons"),
        ]
    }
    no_mismatch = _array_does_not_contain("controlled_variable_checks", "matched", False)
    regressed = _array_contains("scope_comparisons", "comparison_decision", "REGRESSED")
    no_regression = _array_does_not_contain("scope_comparisons", "comparison_decision", "REGRESSED")
    inconclusive = _array_contains("scope_comparisons", "comparison_decision", "INCONCLUSIVE")
    no_inconclusive = _array_does_not_contain("scope_comparisons", "comparison_decision", "INCONCLUSIVE")
    completed: dict[str, JsonValue] = {
        "properties": {"execution_status": {"const": "COMPLETED"}},
        "required": ["execution_status"],
    }

    all_of = document.setdefault("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("comparison schema allOf must be an array")
    all_of.extend(
        [
            {
                "if": invalid_inputs,
                "then": {
                    "properties": {
                        "execution_status": {"const": "INVALID"},
                        "decision_status": {"type": "null"},
                    }
                },
            },
            {
                "if": {"allOf": [no_mismatch, completed, regressed]},
                "then": {"properties": {"decision_status": {"const": "FAIL"}}},
            },
            {
                "if": {"allOf": [no_mismatch, completed, no_regression, inconclusive]},
                "then": {"properties": {"decision_status": {"const": "INCONCLUSIVE"}}},
            },
            {
                "if": {"allOf": [no_mismatch, completed, no_regression, no_inconclusive]},
                "then": {"properties": {"decision_status": {"const": "PASS"}}},
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


def _add_v1_2_review_provenance_conditions(value: JsonValue) -> None:
    if isinstance(value, list):
        for item in value:
            _add_v1_2_review_provenance_conditions(item)
        return
    if not isinstance(value, dict):
        return
    properties = value.get("properties")
    required_properties = {
        "team_gold_status",
        "reviewed_by",
        "reviewed_at",
        "approved_by",
        "approved_at",
        "evidence_review_refs",
    }
    if isinstance(properties, dict) and required_properties.issubset(properties):
        all_of = value.setdefault("allOf", [])
        if not isinstance(all_of, list):
            raise TypeError("ReviewProvenanceV12 allOf must be an array")
        evaluation_reviewer: dict[str, JsonValue] = {
            "allOf": [
                {"not": {"type": "null"}},
                {
                    "type": "object",
                    "properties": {"role": {"const": "EVALUATION_REVIEWER"}},
                    "required": ["role"],
                },
            ]
        }
        all_of.extend(
            [
                {
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
                },
                {
                    "if": {
                        "properties": {"team_gold_status": {"const": "REVIEWED"}},
                        "required": ["team_gold_status"],
                    },
                    "then": {
                        "properties": {
                            "reviewed_by": evaluation_reviewer,
                            "reviewed_at": {"not": {"type": "null"}},
                            "approved_by": {"type": "null"},
                            "approved_at": {"type": "null"},
                            "evidence_review_refs": {"minItems": 1},
                        }
                    },
                },
                {
                    "if": {
                        "properties": {"team_gold_status": {"const": "APPROVED"}},
                        "required": ["team_gold_status"],
                    },
                    "then": {
                        "properties": {
                            "reviewed_by": evaluation_reviewer,
                            "reviewed_at": {"not": {"type": "null"}},
                            "approved_by": {"not": {"type": "null"}},
                            "approved_at": {"not": {"type": "null"}},
                            "evidence_review_refs": {"minItems": 1},
                        }
                    },
                },
            ]
        )
    for item in value.values():
        _add_v1_2_review_provenance_conditions(item)


def _add_v1_2_review_provenance_definition_conditions(document: dict[str, JsonValue]) -> None:
    definitions = document.get("$defs")
    if not isinstance(definitions, dict):
        return
    review_provenance_v1_2 = definitions.get("ReviewProvenanceV12")
    if review_provenance_v1_2 is not None:
        _add_v1_2_review_provenance_conditions(review_provenance_v1_2)


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
    definition_names = [
        name
        for name in definitions
        if name
        in {
            "SafetyCase",
            "EndToEndRagCase",
            "SafetyCaseV11",
            "EndToEndRagCaseV11",
            "SafetyCaseV12",
            "EndToEndRagCaseV12",
        }
    ]
    if len(definition_names) != 2:
        raise TypeError("case schema must contain Safety and End-to-End definitions")
    for definition_name in definition_names:
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


def _add_v1_1_rule_cardinality(definitions: dict[str, JsonValue]) -> None:
    branches: list[JsonValue] = [
        {
            "properties": {
                "expected_rule_outcome": {"const": "MATCHED_RULES"},
                "expected_rule_ids": {"minItems": 1},
                "expected_rule_not_invoked_reason": {"type": "null"},
            },
            "required": ["expected_rule_outcome", "expected_rule_ids", "expected_rule_not_invoked_reason"],
        },
        {
            "properties": {
                "expected_rule_outcome": {"const": "NO_MATCH"},
                "expected_rule_ids": {"maxItems": 0},
                "expected_rule_not_invoked_reason": {"type": "null"},
            },
            "required": ["expected_rule_outcome", "expected_rule_ids", "expected_rule_not_invoked_reason"],
        },
        {
            "properties": {
                "expected_rule_outcome": {"const": "NOT_INVOKED"},
                "expected_rule_ids": {"maxItems": 0},
                "expected_rule_not_invoked_reason": {"not": {"type": "null"}},
            },
            "required": ["expected_rule_outcome", "expected_rule_ids", "expected_rule_not_invoked_reason"],
        },
    ]
    for definition_name in ("SafetyExpectedV11", "EndToEndRagExpectedV11"):
        expected = definitions.get(definition_name)
        if not isinstance(expected, dict):
            raise TypeError(f"{definition_name} schema must be an object")
        expected["oneOf"] = branches


def _nested_case_condition(
    *,
    expected_if: dict[str, JsonValue],
    runtime_if: dict[str, JsonValue] | None = None,
    expected_then: dict[str, JsonValue] | None = None,
    runtime_then: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    if_properties: dict[str, JsonValue] = {}
    if expected_if:
        if_properties["expected"] = {"properties": expected_if, "required": list(expected_if)}
    if runtime_if is not None:
        if_properties["context"] = {
            "properties": {"runtime_fixture": {"properties": runtime_if, "required": list(runtime_if)}},
            "required": ["runtime_fixture"],
        }
    then_properties: dict[str, JsonValue] = {}
    if expected_then is not None:
        then_properties["expected"] = {"properties": expected_then}
    if runtime_then is not None:
        then_properties["context"] = {
            "properties": {"runtime_fixture": {"properties": runtime_then}},
            "required": ["runtime_fixture"],
        }
    return {
        "if": {
            "properties": if_properties,
            "required": list(if_properties),
        },
        "then": {"properties": then_properties},
    }


def _add_v1_1_case_context_conditions(definitions: dict[str, JsonValue]) -> None:
    runtime = definitions.get("RuntimeFixtureV11")
    if not isinstance(runtime, dict):
        raise TypeError("RuntimeFixtureV11 schema must be an object")
    runtime["oneOf"] = [
        {
            "properties": {
                "source_eligibility_status": {"const": "ELIGIBLE"},
                "bundle_eligibility_status": {"not": {"const": "SOURCE_INELIGIBLE"}},
            },
            "required": ["source_eligibility_status", "bundle_eligibility_status"],
        },
        {
            "properties": {
                "source_eligibility_status": {"not": {"const": "ELIGIBLE"}},
                "bundle_eligibility_status": {"const": "SOURCE_INELIGIBLE"},
            },
            "required": ["source_eligibility_status", "bundle_eligibility_status"],
        },
    ]
    conditions = [
        _nested_case_condition(
            expected_if={"expected_rule_outcome": {"enum": ["MATCHED_RULES", "NO_MATCH"]}},
            runtime_then={
                "source_eligibility_status": {"const": "ELIGIBLE"},
                "bundle_eligibility_status": {"const": "ELIGIBLE"},
            },
        ),
        _nested_case_condition(
            expected_if={"expected_rule_outcome": {"const": "NOT_INVOKED"}},
            expected_then={
                "expected_provider_invocation": {"const": False},
                "expected_retrieval_invocation": {"const": False},
            },
            runtime_then={"dependency_fault": {"const": "NONE"}},
        ),
        _nested_case_condition(
            expected_if={"expected_rule_not_invoked_reason": {"const": "SAFETY_ROUTED"}},
            expected_then={"expected_safety_disposition": {"not": {"const": "NORMAL"}}},
            runtime_then={
                "source_eligibility_status": {"const": "ELIGIBLE"},
                "bundle_eligibility_status": {"const": "ELIGIBLE"},
            },
        ),
        _nested_case_condition(
            expected_if={"expected_rule_not_invoked_reason": {"const": "SOURCE_INELIGIBLE"}},
            runtime_then={"source_eligibility_status": {"not": {"const": "ELIGIBLE"}}},
        ),
        _nested_case_condition(
            expected_if={"expected_rule_not_invoked_reason": {"const": "BUNDLE_INELIGIBLE"}},
            runtime_then={"bundle_eligibility_status": {"enum": ["SCOPE_INELIGIBLE", "MEMBER_INELIGIBLE"]}},
        ),
        _nested_case_condition(
            expected_if={},
            expected_then={
                "expected_execution_status": {"const": "TIMED_OUT"},
                "expected_provider_invocation": {"const": True},
            },
            runtime_if={"dependency_fault": {"const": "PROVIDER_TIMEOUT"}},
        ),
        _nested_case_condition(
            expected_if={},
            expected_then={
                "expected_execution_status": {"const": "DEPENDENCY_ERROR"},
                "expected_retrieval_invocation": {"const": True},
            },
            runtime_if={"dependency_fault": {"const": "RETRIEVAL_FAILURE"}},
        ),
    ]
    for definition_name in (
        "SafetyCaseV11",
        "EndToEndRagCaseV11",
        "SafetyCaseV12",
        "EndToEndRagCaseV12",
    ):
        case = definitions.get(definition_name)
        if case is None:
            continue
        if not isinstance(case, dict):
            raise TypeError(f"{definition_name} schema must be an object")
        all_of = case.setdefault("allOf", [])
        if not isinstance(all_of, list):
            raise TypeError(f"{definition_name} allOf must be an array")
        all_of.extend(conditions)


def _add_versioned_authoring_conditions(entry: SchemaRegistryEntry, document: dict[str, JsonValue]) -> None:
    if entry.schema_id != "rag-eval.case" or entry.member_version not in {"1.1.0", "1.2.0"}:
        return
    definitions = document.get("$defs")
    if not isinstance(definitions, dict):
        raise TypeError("case schema definitions must be an object")
    _add_v1_1_rule_cardinality(definitions)
    _add_v1_1_case_context_conditions(definitions)


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
    _add_v1_2_review_provenance_definition_conditions(document)
    _add_authoring_role_conditions(schema_id, document)
    _add_versioned_authoring_conditions(entry, document)
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
    _add_empty_aggregate_conditions(schema_id, document)
    _add_aggregate_outcome_conditions(schema_id, document)
    if schema_id == "rag-eval.gate":
        _add_gate_member_conditions(document)
    if schema_id == "rag-eval.comparison":
        _add_comparison_outcome_conditions(document)
    return normalize_schema_document(document)


def schema_documents(schema_set_version: str = SCHEMA_VERSION) -> dict[str, dict[str, JsonValue]]:
    registry = SCHEMA_REGISTRIES[schema_set_version]
    return dict(sorted((entry.relative_path, _schema_document(entry)) for entry in registry))


def write_schema_documents(root: Path, schema_set_version: str = SCHEMA_VERSION) -> None:
    documents = schema_documents(schema_set_version)
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
    parser.add_argument("--schema-set-version", choices=tuple(SCHEMA_REGISTRIES), default=SCHEMA_VERSION)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    write_schema_documents(args.output, args.schema_set_version)


if __name__ == "__main__":
    main()
