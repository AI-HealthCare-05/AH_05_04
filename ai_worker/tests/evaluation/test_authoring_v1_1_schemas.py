from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import (
    EVALUATION_CASE_ADAPTER_V1_1,
    RuleExpectedOutcome,
)

EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _safety_case() -> dict[str, Any]:
    path = EVALS_ROOT / "retrieval/cases/dev-foundation-v1/rag-dev-safety-001.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "1.1.0"
    payload["context"]["runtime_fixture"].update(
        {
            "source_eligibility_status": "ELIGIBLE",
            "bundle_eligibility_status": "ELIGIBLE",
            "dependency_fault": "NONE",
        }
    )
    payload["expected"].update(
        {
            "expected_rule_outcome": "MATCHED_RULES",
            "expected_rule_not_invoked_reason": None,
        }
    )
    return payload


def test_matched_rules_requires_one_or_more_rule_ids() -> None:
    payload = _safety_case()

    case = EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)

    assert case.expected.expected_rule_outcome is RuleExpectedOutcome.MATCHED_RULES
    assert case.expected.expected_rule_ids == ("ev-synthetic-rule-001",)

    payload["expected"]["expected_rule_ids"] = []
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


def test_no_match_requires_empty_rule_ids_and_healthy_rule_inputs() -> None:
    payload = _safety_case()
    payload["expected"].update(
        {
            "expected_rule_outcome": "NO_MATCH",
            "expected_rule_ids": [],
        }
    )

    case = EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)
    assert case.expected.expected_rule_ids == ()

    with_rule = deepcopy(payload)
    with_rule["expected"]["expected_rule_ids"] = ["ev-synthetic-rule-001"]
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(with_rule)

    ineligible = deepcopy(payload)
    ineligible["context"]["runtime_fixture"]["source_eligibility_status"] = "EXPIRED"
    ineligible["context"]["runtime_fixture"]["bundle_eligibility_status"] = "SOURCE_INELIGIBLE"
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(ineligible)


def test_matched_rules_rejects_ineligible_rule_inputs() -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="EXPIRED",
        bundle_eligibility_status="SOURCE_INELIGIBLE",
    )

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


@pytest.mark.parametrize(
    ("reason", "runtime_updates"),
    [
        (
            "SOURCE_INELIGIBLE",
            {"source_eligibility_status": "EXPIRED", "bundle_eligibility_status": "SOURCE_INELIGIBLE"},
        ),
        ("BUNDLE_INELIGIBLE", {"bundle_eligibility_status": "SCOPE_INELIGIBLE"}),
    ],
)
def test_not_invoked_requires_empty_ids_and_matching_typed_reason(
    reason: str,
    runtime_updates: dict[str, str],
) -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"].update(runtime_updates)
    payload["expected"].update(
        {
            "expected_rule_outcome": "NOT_INVOKED",
            "expected_rule_ids": [],
            "expected_rule_not_invoked_reason": reason,
            "expected_provider_invocation": False,
            "expected_retrieval_invocation": False,
        }
    )

    EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)

    payload["expected"]["expected_rule_not_invoked_reason"] = (
        "BUNDLE_INELIGIBLE" if reason == "SOURCE_INELIGIBLE" else "SOURCE_INELIGIBLE"
    )
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


def test_safety_routed_not_invoked_requires_non_normal_safety_and_no_general_pipeline() -> None:
    payload = _safety_case()
    payload["expected"].update(
        {
            "expected_rule_outcome": "NOT_INVOKED",
            "expected_rule_ids": [],
            "expected_rule_not_invoked_reason": "SAFETY_ROUTED",
            "expected_provider_invocation": False,
            "expected_retrieval_invocation": False,
        }
    )

    EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)

    payload["expected"]["expected_safety_disposition"] = "NORMAL"
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


@pytest.mark.parametrize(
    ("fault", "execution_status", "invocation_field"),
    [
        ("PROVIDER_TIMEOUT", "TIMED_OUT", "expected_provider_invocation"),
        ("RETRIEVAL_FAILURE", "DEPENDENCY_ERROR", "expected_retrieval_invocation"),
    ],
)
def test_dependency_fault_requires_matching_execution_status_and_invocation(
    fault: str,
    execution_status: str,
    invocation_field: str,
) -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"]["dependency_fault"] = fault

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)

    payload["expected"]["expected_execution_status"] = execution_status
    payload["expected"][invocation_field] = True
    EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


def test_dependency_failure_is_not_a_rule_not_invoked_reason() -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"]["dependency_fault"] = "PROVIDER_TIMEOUT"
    payload["expected"].update(
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=[],
        expected_rule_not_invoked_reason="DEPENDENCY_FAILURE",
        expected_execution_status="TIMED_OUT",
        expected_provider_invocation=True,
    )

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


def test_not_invoked_rejects_dependency_fault_after_rule_first() -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="EXPIRED",
        bundle_eligibility_status="SOURCE_INELIGIBLE",
        dependency_fault="PROVIDER_TIMEOUT",
    )
    payload["expected"].update(
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=[],
        expected_rule_not_invoked_reason="SOURCE_INELIGIBLE",
        expected_execution_status="TIMED_OUT",
        expected_provider_invocation=True,
    )

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


@pytest.mark.parametrize("invocation_field", ["expected_provider_invocation", "expected_retrieval_invocation"])
def test_not_invoked_rejects_general_pipeline_invocation(invocation_field: str) -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="EXPIRED",
        bundle_eligibility_status="SOURCE_INELIGIBLE",
    )
    payload["expected"].update(
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=[],
        expected_rule_not_invoked_reason="SOURCE_INELIGIBLE",
        expected_provider_invocation=False,
        expected_retrieval_invocation=False,
    )
    payload["expected"][invocation_field] = True

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


@pytest.mark.parametrize("outcome", ["NO_MATCH", "NOT_INVOKED"])
def test_rule_outcomes_keep_nonempty_request_scope(outcome: str) -> None:
    payload = _safety_case()
    payload["expected"].update(
        expected_rule_outcome=outcome,
        expected_rule_ids=[],
        expected_rule_not_invoked_reason="SAFETY_ROUTED" if outcome == "NOT_INVOKED" else None,
    )
    if outcome == "NOT_INVOKED":
        payload["expected"].update(
            expected_provider_invocation=False,
            expected_retrieval_invocation=False,
        )

    EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)

    payload["expected"]["expected_scope_codes"] = []
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


@pytest.mark.parametrize(
    ("fixture", "task_type"),
    [
        ("rag-dev-answer-quality-001.json", "ANSWER_QUALITY"),
        ("rag-dev-answer-grounding-001.json", "ANSWER_GROUNDING"),
    ],
)
def test_answer_only_cases_reject_rule_ids_without_rule_outcome(fixture: str, task_type: str) -> None:
    path = EVALS_ROOT / "retrieval/cases/dev-foundation-v1" / fixture
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "1.1.0"
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="ELIGIBLE",
        bundle_eligibility_status="ELIGIBLE",
        dependency_fault="NONE",
    )
    payload["expected"].update(
        expected_rule_outcome=None,
        expected_rule_not_invoked_reason=None,
    )

    EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)

    payload["expected"].update(
        expected_rule_ids=["ev-synthetic-rule-001"],
    )

    assert payload["task_type"] == task_type
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


def test_bundle_not_invoked_reason_does_not_mask_a_source_failure() -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="EXPIRED",
        bundle_eligibility_status="SOURCE_INELIGIBLE",
    )
    payload["expected"].update(
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=[],
        expected_rule_not_invoked_reason="BUNDLE_INELIGIBLE",
    )

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


def test_runtime_fixture_rejects_contradictory_source_and_bundle_eligibility() -> None:
    payload = _safety_case()
    payload["context"]["runtime_fixture"]["source_eligibility_status"] = "INACTIVE"

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


def test_identity_insufficient_remains_outside_evaluation_case() -> None:
    payload = _safety_case()
    payload["context"]["medication_fixtures"][0]["identification_status"] = "AMBIGUOUS"

    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)
