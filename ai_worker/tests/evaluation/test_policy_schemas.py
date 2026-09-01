from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType
from typing import Any, cast

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.schemas.policy import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)


def _actor(actor_id: str, display_name: str) -> dict[str, object]:
    return {"namespace": "github", "actor_id": actor_id, "display_name": display_name}


def _review_provenance() -> dict[str, object]:
    return {
        "proposed_by": _actor("rag-owner", "RAG owner"),
        "approved_by": _actor("product-approver", "Product approver"),
        "reviewed_at": "2026-09-01T00:00:00Z",
    }


@pytest.fixture
def profile_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "profile_code": "rag-release",
        "profile_version": "1.0.0",
        "runtime_eligible": True,
        "required_experiment_types": ["END_TO_END_RAG"],
        "required_partitions": ["HOLDOUT", "SAFETY_REGRESSION"],
        "suite_references": [
            {
                "resource_id": "rag-eval.suite.release",
                "resource_version": "1.0.0",
                "resource_hash": "a" * 64,
            }
        ],
        "review_provenance": _review_provenance(),
        "content_hash": "b" * 64,
    }


@pytest.fixture
def suite_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "suite_code": "rag-release",
        "suite_version": "1.0.0",
        "experiment_type": "END_TO_END_RAG",
        "partitions": ["HOLDOUT", "SAFETY_REGRESSION"],
        "task_types": ["END_TO_END_RAG", "SAFETY"],
        "required": True,
        "review_provenance": _review_provenance(),
        "content_hash": "c" * 64,
    }


@pytest.fixture
def policy_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "policy_code": "rag-release-comparison",
        "policy_version": "1.0.0",
        "scopes": [
            {
                "metric_code": "citation-precision",
                "metric_version": "1.0.0",
                "partition": "HOLDOUT",
                "slice_key": "ALL",
                "required": True,
                "analysis_unit": "CASE",
                "estimator": "PROPORTION",
                "minimum_case_count": 30,
                "minimum_independent_group_count": 20,
                "cluster_dimension": "question_template",
                "threshold": "0.95",
                "decision_basis": "CANDIDATE_THRESHOLD",
                "ci_method": "CLUSTER_BOOTSTRAP",
                "ci_method_version": "1.0.0",
                "ci_parameters": {"confidence_level": "0.95", "resamples": 10000},
                "seed": None,
            }
        ],
        "proposed_by": _actor("rag-owner", "RAG owner"),
        "approved_by": _actor("product-approver", "Product approver"),
        "reviewed_at": "2026-09-01T00:00:00Z",
        "content_hash": "d" * 64,
    }


def _policy_members() -> list[dict[str, Any]]:
    return [
        {
            "member_order": 1,
            "member_type": "PROFILE",
            "reference": {
                "resource_id": "rag-eval.profile.release",
                "resource_version": "1.0.0",
                "resource_hash": "a" * 64,
            },
        },
        {
            "member_order": 2,
            "member_type": "SUITE",
            "reference": {
                "resource_id": "rag-eval.suite.release",
                "resource_version": "1.0.0",
                "resource_hash": "b" * 64,
            },
        },
        {
            "member_order": 3,
            "member_type": "COMPARISON_POLICY",
            "reference": {
                "resource_id": "rag-eval.comparison.release",
                "resource_version": "1.0.0",
                "resource_hash": "c" * 64,
            },
        },
    ]


def _evaluation_policy_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "policy_code": "rag-release",
        "policy_version": "1.0.0",
        "members": _policy_members(),
        "member_manifest_hash": "9823dffe940e9b29971afe6407b892838858c64788df5a8494ee0ba2f7898621",
        "review_provenance": _review_provenance(),
        "content_hash": "e" * 64,
    }


def test_release_profile_requires_end_to_end_holdout_and_safety(
    profile_payload: dict[str, Any],
) -> None:
    EvaluationProfile.model_validate(profile_payload)

    missing_experiment = deepcopy(profile_payload)
    missing_experiment["required_experiment_types"] = ["KNOWLEDGE_RETRIEVAL"]
    with pytest.raises(ValidationError):
        EvaluationProfile.model_validate(missing_experiment)

    for missing_partition in ("HOLDOUT", "SAFETY_REGRESSION"):
        missing_partition_payload = deepcopy(profile_payload)
        missing_partition_payload["required_partitions"].remove(missing_partition)
        with pytest.raises(ValidationError):
            EvaluationProfile.model_validate(missing_partition_payload)


def test_non_runtime_profile_may_define_diagnostic_scope(profile_payload: dict[str, Any]) -> None:
    profile_payload["runtime_eligible"] = False
    profile_payload["required_experiment_types"] = ["KNOWLEDGE_RETRIEVAL"]
    profile_payload["required_partitions"] = ["DEV"]

    profile = EvaluationProfile.model_validate(profile_payload)

    assert profile.runtime_eligible is False


def test_suite_definition_accepts_complete_wire_payload(suite_payload: dict[str, Any]) -> None:
    suite = SuiteDefinition.model_validate(suite_payload)

    assert suite.experiment_type.value == "END_TO_END_RAG"
    assert [partition.value for partition in suite.partitions] == ["HOLDOUT", "SAFETY_REGRESSION"]


def test_profile_repeated_fields_are_deeply_immutable(profile_payload: dict[str, Any]) -> None:
    profile = EvaluationProfile.model_validate(profile_payload)

    with pytest.raises(AttributeError):
        cast(Any, profile.required_partitions).append(profile.required_partitions[0])


def test_suite_repeated_fields_are_deeply_immutable(suite_payload: dict[str, Any]) -> None:
    suite = SuiteDefinition.model_validate(suite_payload)

    with pytest.raises(AttributeError):
        cast(Any, suite.task_types).pop()


def test_comparison_policy_rejects_self_approval(policy_payload: dict[str, Any]) -> None:
    ComparisonPolicy.model_validate(policy_payload)
    policy_payload["approved_by"] = policy_payload["proposed_by"]

    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(policy_payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_case_count", 0),
        ("minimum_independent_group_count", 0),
        ("cluster_dimension", "patient_id"),
        ("threshold", "0.950"),
        ("ci_parameters", {"confidence_level": 0.95}),
    ],
)
def test_comparison_scope_rejects_noncanonical_or_insufficient_policy_values(
    policy_payload: dict[str, Any],
    field: str,
    value: object,
) -> None:
    policy_payload["scopes"][0][field] = value

    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(policy_payload)


def test_comparison_scope_accepts_nullable_safe_integer_seed(policy_payload: dict[str, Any]) -> None:
    without_seed = ComparisonPolicy.model_validate(policy_payload)
    policy_payload["scopes"][0]["seed"] = 1729
    with_seed = ComparisonPolicy.model_validate(policy_payload)

    assert without_seed.scopes[0].seed is None
    assert with_seed.scopes[0].seed == 1729


def test_wilson_score_ci_accepts_generic_parameters_without_resamples(
    policy_payload: dict[str, Any],
) -> None:
    scope = policy_payload["scopes"][0]
    scope["ci_method"] = "WILSON_SCORE"
    scope["ci_parameters"] = {
        "confidence_level": "0.95",
        "continuity_correction": True,
    }

    policy = ComparisonPolicy.model_validate(policy_payload)

    assert policy.model_dump(mode="json")["scopes"][0]["ci_parameters"] == {
        "confidence_level": "0.95",
        "continuity_correction": True,
    }


def test_bootstrap_ci_accepts_extra_method_specific_scalar_parameters(
    policy_payload: dict[str, Any],
) -> None:
    policy_payload["scopes"][0]["ci_parameters"] = {
        "confidence_level": "0.95",
        "resamples": 10000,
        "stratification": "cluster",
        "studentized": False,
        "optional_tuning": None,
    }

    policy = ComparisonPolicy.model_validate(policy_payload)

    assert policy.model_dump(mode="json")["scopes"][0]["ci_parameters"]["stratification"] == "cluster"


@pytest.mark.parametrize("nested_value", [["cluster"], {"mode": "cluster"}])
def test_ci_parameters_reject_nested_collections(
    policy_payload: dict[str, Any],
    nested_value: object,
) -> None:
    policy_payload["scopes"][0]["ci_parameters"]["nested"] = nested_value

    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(policy_payload)


def test_ci_parameter_storage_and_hash_are_independent_of_wire_key_order(
    policy_payload: dict[str, Any],
) -> None:
    first_payload = deepcopy(policy_payload)
    first_payload["scopes"][0]["ci_parameters"] = {
        "confidence_level": "0.95",
        "resamples": 10000,
        "studentized": False,
    }
    reversed_payload = deepcopy(policy_payload)
    reversed_payload["scopes"][0]["ci_parameters"] = {
        "studentized": False,
        "resamples": 10000,
        "confidence_level": "0.95",
    }

    first = ComparisonPolicy.model_validate(first_payload)
    reversed_order = ComparisonPolicy.model_validate(reversed_payload)
    first_json = first.model_dump(mode="json")
    reversed_json = reversed_order.model_dump(mode="json")

    assert first.scopes[0].ci_parameters == reversed_order.scopes[0].ci_parameters
    assert first_json == reversed_json
    assert canonical_sha256(first_json) == canonical_sha256(reversed_json)


@pytest.mark.parametrize(
    "non_object",
    [
        (("confidence_level", "0.95"), ("confidence_level", "0.9")),
        [["confidence_level", "0.95"]],
        MappingProxyType({"confidence_level": "0.95"}),
    ],
)
def test_ci_parameters_reject_non_dict_and_duplicate_pair_bypasses(
    policy_payload: dict[str, Any],
    non_object: object,
) -> None:
    policy_payload["scopes"][0]["ci_parameters"] = non_object

    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(policy_payload)


@pytest.mark.parametrize(
    "invalid_parameters",
    [
        {1: "integer-key"},
        {"valid": "value", 1: "mixed-key"},
    ],
)
def test_ci_parameter_invalid_keys_raise_validation_error_not_raw_type_error(
    policy_payload: dict[str, Any],
    invalid_parameters: dict[object, object],
) -> None:
    policy_payload["scopes"][0]["ci_parameters"] = invalid_parameters

    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(policy_payload)


def test_ci_parameters_reject_empty_keys_at_runtime(policy_payload: dict[str, Any]) -> None:
    policy_payload["scopes"][0]["ci_parameters"] = {"": "empty-key"}

    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(policy_payload)


def test_ci_parameters_json_schema_rejects_empty_property_names() -> None:
    comparison_schema = ComparisonPolicy.model_json_schema()
    ci_parameters_schema = comparison_schema["$defs"]["ComparisonScope"]["properties"]["ci_parameters"]

    assert ci_parameters_schema["propertyNames"] == {"type": "string", "minLength": 1}


def test_comparison_policy_scopes_are_deeply_immutable(policy_payload: dict[str, Any]) -> None:
    policy = ComparisonPolicy.model_validate(policy_payload)

    with pytest.raises(AttributeError):
        cast(Any, policy.scopes).pop()


def test_comparison_scope_ci_parameters_are_deeply_immutable(policy_payload: dict[str, Any]) -> None:
    policy = ComparisonPolicy.model_validate(policy_payload)

    with pytest.raises((TypeError, ValidationError)):
        cast(Any, policy.scopes[0].ci_parameters)["confidence_level"] = "0.9"


def test_evaluation_policy_validates_hand_checked_member_manifest_hash() -> None:
    policy = EvaluationPolicy.model_validate(_evaluation_policy_payload())

    assert policy.member_manifest_hash == "9823dffe940e9b29971afe6407b892838858c64788df5a8494ee0ba2f7898621"


def test_evaluation_policy_rejects_duplicate_member_natural_key() -> None:
    payload = _evaluation_policy_payload()
    payload["members"][1]["reference"] = deepcopy(payload["members"][0]["reference"])
    payload["members"][1]["member_type"] = payload["members"][0]["member_type"]

    with pytest.raises(ValidationError):
        EvaluationPolicy.model_validate(payload)


def test_evaluation_policy_rejects_duplicate_member_order() -> None:
    payload = _evaluation_policy_payload()
    payload["members"][1]["member_order"] = 1

    with pytest.raises(ValidationError):
        EvaluationPolicy.model_validate(payload)


def test_evaluation_policy_rejects_mismatched_member_manifest_hash() -> None:
    payload = _evaluation_policy_payload()
    payload["member_manifest_hash"] = "f" * 64

    with pytest.raises(ValidationError):
        EvaluationPolicy.model_validate(payload)


def test_evaluation_policy_members_remain_unchanged_after_mutation_attempt() -> None:
    policy = EvaluationPolicy.model_validate(_evaluation_policy_payload())
    original_members = tuple(policy.members)

    with pytest.raises(AttributeError):
        cast(Any, policy.members).pop()

    assert tuple(policy.members) == original_members
    assert policy.member_manifest_hash == "9823dffe940e9b29971afe6407b892838858c64788df5a8494ee0ba2f7898621"


def test_immutable_collections_preserve_json_array_and_object_wire_shapes(
    profile_payload: dict[str, Any],
    suite_payload: dict[str, Any],
    policy_payload: dict[str, Any],
) -> None:
    profile_json = EvaluationProfile.model_validate(profile_payload).model_dump(mode="json")
    suite_json = SuiteDefinition.model_validate(suite_payload).model_dump(mode="json")
    comparison_json = ComparisonPolicy.model_validate(policy_payload).model_dump(mode="json")
    evaluation_json = EvaluationPolicy.model_validate(_evaluation_policy_payload()).model_dump(mode="json")

    assert profile_json["required_partitions"] == ["HOLDOUT", "SAFETY_REGRESSION"]
    assert suite_json["task_types"] == ["END_TO_END_RAG", "SAFETY"]
    assert comparison_json["scopes"][0]["ci_parameters"] == {
        "confidence_level": "0.95",
        "resamples": 10000,
    }
    assert [member["member_order"] for member in evaluation_json["members"]] == [1, 2, 3]

    assert EvaluationProfile.model_json_schema()["properties"]["required_partitions"]["type"] == "array"
    assert SuiteDefinition.model_json_schema()["properties"]["task_types"]["type"] == "array"
    comparison_schema = ComparisonPolicy.model_json_schema()
    assert comparison_schema["properties"]["scopes"]["type"] == "array"
    assert comparison_schema["$defs"]["ComparisonScope"]["properties"]["ci_parameters"]["type"] == "object"
    assert EvaluationPolicy.model_json_schema()["properties"]["members"]["type"] == "array"
