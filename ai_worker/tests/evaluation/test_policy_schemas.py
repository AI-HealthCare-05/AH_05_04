from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

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
