from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.schemas.policy_contract import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)


def _actor(
    actor_id: str,
    _display_name: str,
    role: str = "EVALUATION_IMPLEMENTER",
) -> dict[str, object]:
    return {
        "namespace": "GITHUB_LOGIN",
        "actor_id": actor_id,
        "role": role,
    }


def _review_provenance() -> dict[str, object]:
    return {
        "authored_by": _actor("ceohwj", "RAG owner"),
        "reviewed_by": _actor("hazelnutflavoured", "Policy reviewer", "MEDICAL_REVIEWER"),
        "approved_by": None,
        "authored_at": "2026-09-01T00:00:00.000000Z",
        "reviewed_at": "2026-09-01T00:00:00.000000Z",
        "approved_at": None,
        "team_gold_status": "REVIEWED",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }


@pytest.fixture
def profile_payload() -> dict[str, Any]:
    return _fixture_json("profiles/dev-foundation-v1.profile.json")


@pytest.fixture
def suite_payload() -> dict[str, Any]:
    return _fixture_json("suites/dev-foundation-v1.suite.json")


@pytest.fixture
def policy_payload() -> dict[str, Any]:
    return _fixture_json("policies/dev-foundation-v1.comparison-policy.json")


EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _fixture_json(relative_path: str) -> dict[str, Any]:
    return json.loads((EVALS_ROOT / relative_path).read_text(encoding="utf-8"))


def _policy_members() -> list[dict[str, Any]]:
    policy = _fixture_json("policies/dev-foundation-v1.evaluation-policy.json")
    return [
        policy["evaluation_profile_ref"],
        policy["comparison_policy_ref"],
        *policy["required_partition_refs"],
        *policy["required_gate_refs"],
        *policy["required_suite_refs"],
        policy["artifact_schema_set_ref"],
    ]


def _evaluation_policy_payload() -> dict[str, Any]:
    return _fixture_json("policies/dev-foundation-v1.evaluation-policy.json")


def test_release_profile_requires_end_to_end_holdout_and_safety(
    profile_payload: dict[str, Any],
) -> None:
    profile_payload["runtime_eligible"] = True
    profile_payload["required_experiment_types"] = ["END_TO_END_RAG"]
    profile_payload["required_partitions"] = ["HOLDOUT", "SAFETY_REGRESSION"]
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

    assert suite.adapter_id == "validation-only.v1"
    assert [partition.value for partition in suite.input_selector.partitions] == ["DEV"]


def test_profile_repeated_fields_are_deeply_immutable(profile_payload: dict[str, Any]) -> None:
    profile = EvaluationProfile.model_validate(profile_payload)

    with pytest.raises(AttributeError):
        cast(Any, profile.required_partitions).append(profile.required_partitions[0])


def test_suite_repeated_fields_are_deeply_immutable(suite_payload: dict[str, Any]) -> None:
    suite = SuiteDefinition.model_validate(suite_payload)

    with pytest.raises(AttributeError):
        cast(Any, suite.input_selector.task_types).pop()


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
    scope["ci_method_id"] = "WILSON_SCORE"
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
    payload = _evaluation_policy_payload()
    policy = EvaluationPolicy.model_validate(payload)

    assert policy.member_manifest_hash == payload["member_manifest_hash"]


def test_evaluation_policy_rejects_duplicate_member_natural_key() -> None:
    payload = _evaluation_policy_payload()
    payload["required_suite_refs"][0]["reference"] = deepcopy(payload["evaluation_profile_ref"]["reference"])
    payload["required_suite_refs"][0]["member_type"] = "PROFILE"

    with pytest.raises(ValidationError):
        EvaluationPolicy.model_validate(payload)


def test_evaluation_policy_rejects_duplicate_member_order() -> None:
    payload = _evaluation_policy_payload()
    payload["comparison_policy_ref"]["member_order"] = 1

    with pytest.raises(ValidationError):
        EvaluationPolicy.model_validate(payload)


def test_evaluation_policy_rejects_mismatched_member_manifest_hash() -> None:
    payload = _evaluation_policy_payload()
    payload["member_manifest_hash"] = "f" * 64

    with pytest.raises(ValidationError):
        EvaluationPolicy.model_validate(payload)


def test_evaluation_policy_members_remain_unchanged_after_mutation_attempt() -> None:
    payload = _evaluation_policy_payload()
    policy = EvaluationPolicy.model_validate(payload)
    original_members = tuple(policy.members)

    with pytest.raises(AttributeError):
        cast(Any, policy.members).pop()

    assert tuple(policy.members) == original_members
    assert policy.member_manifest_hash == payload["member_manifest_hash"]


def test_immutable_collections_preserve_json_array_and_object_wire_shapes(
    profile_payload: dict[str, Any],
    suite_payload: dict[str, Any],
    policy_payload: dict[str, Any],
) -> None:
    profile_json = EvaluationProfile.model_validate(profile_payload).model_dump(mode="json")
    suite_json = SuiteDefinition.model_validate(suite_payload).model_dump(mode="json")
    comparison_json = ComparisonPolicy.model_validate(policy_payload).model_dump(mode="json")
    evaluation_json = EvaluationPolicy.model_validate(_evaluation_policy_payload()).model_dump(mode="json")

    assert profile_json["required_partitions"] == ["DEV"]
    assert suite_json["input_selector"]["task_types"] == [
        "ANSWER_GROUNDING",
        "ANSWER_QUALITY",
        "END_TO_END_RAG",
        "RETRIEVAL",
        "SAFETY",
    ]
    assert comparison_json["scopes"][0]["ci_parameters"] == {
        "validation_only": True,
    }
    assert evaluation_json["evaluation_profile_ref"]["member_order"] == 1
    assert evaluation_json["artifact_schema_set_ref"]["member_order"] == 5

    assert EvaluationProfile.model_json_schema()["properties"]["required_partitions"]["type"] == "array"
    suite_schema = SuiteDefinition.model_json_schema()
    assert suite_schema["$defs"]["SuiteInputSelector"]["properties"]["task_types"]["type"] == "array"
    comparison_schema = ComparisonPolicy.model_json_schema()
    assert comparison_schema["properties"]["scopes"]["type"] == "array"
    assert comparison_schema["$defs"]["ComparisonScope"]["properties"]["ci_parameters"]["type"] == "object"
    assert EvaluationPolicy.model_json_schema()["properties"]["required_suite_refs"]["type"] == "array"
