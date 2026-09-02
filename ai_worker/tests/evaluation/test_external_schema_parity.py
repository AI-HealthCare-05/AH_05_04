from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from ai_worker.tasks.evaluation.schemas.artifacts import ComparisonResult, GateResult, MetricResults, SuiteResults
from ai_worker.tasks.evaluation.schemas.authoring import DatasetManifest, EvidenceMappingManifest
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import EVALUATION_CASE_ADAPTER_V1_1
from ai_worker.tests.evaluation.test_artifact_schemas import (
    _comparison_payload,
    _gate_payload,
    _metric_payload,
    _suite_payload,
)

jsonschema: Any = pytest.importorskip(
    "jsonschema",
    reason="external schema parity tests require the optional jsonschema validator",
)

EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _json(relative_path: str) -> dict[str, Any]:
    return json.loads((EVALS_ROOT / relative_path).read_text(encoding="utf-8"))


def _assert_external_runtime_parity(
    *,
    schema_path: str,
    payload: dict[str, Any],
    runtime_model: type[BaseModel],
    expected_valid: bool,
) -> None:
    schema = _json(schema_path)
    external_errors = list(jsonschema.Draft202012Validator(schema).iter_errors(payload))
    if expected_valid:
        assert external_errors == []
        runtime_model.model_validate(payload)
        return

    assert external_errors
    with pytest.raises(ValidationError):
        runtime_model.model_validate(payload)


def _v1_1_safety_case() -> dict[str, Any]:
    payload = _json("retrieval/cases/dev-foundation-v1/rag-dev-safety-001.json")
    payload["schema_version"] = "1.1.0"
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="ELIGIBLE",
        bundle_eligibility_status="ELIGIBLE",
        dependency_fault="NONE",
    )
    payload["expected"].update(
        expected_rule_outcome="MATCHED_RULES",
        expected_rule_not_invoked_reason=None,
    )
    return payload


@pytest.mark.parametrize(
    "mutation",
    [
        "matched-without-rules",
        "no-match-with-rules",
        "not-invoked-without-reason",
        "source-axis-contradiction",
        "reason-context-mismatch",
        "bundle-reason-masks-source",
        "fault-status-mismatch",
        "not-invoked-with-dependency-fault",
        "matched-with-ineligible-inputs",
        "not-invoked-with-provider-invocation",
        "not-invoked-with-retrieval-invocation",
    ],
)
def test_external_v1_1_case_schema_matches_runtime_rule_and_fixture_invariants(mutation: str) -> None:
    payload = _v1_1_safety_case()
    if mutation == "matched-without-rules":
        payload["expected"]["expected_rule_ids"] = []
    elif mutation == "no-match-with-rules":
        payload["expected"]["expected_rule_outcome"] = "NO_MATCH"
    elif mutation == "not-invoked-without-reason":
        payload["expected"].update(expected_rule_outcome="NOT_INVOKED", expected_rule_ids=[])
    elif mutation == "source-axis-contradiction":
        payload["context"]["runtime_fixture"]["source_eligibility_status"] = "EXPIRED"
    elif mutation == "reason-context-mismatch":
        payload["expected"].update(
            expected_rule_outcome="NOT_INVOKED",
            expected_rule_ids=[],
            expected_rule_not_invoked_reason="BUNDLE_INELIGIBLE",
        )
    elif mutation == "bundle-reason-masks-source":
        payload["context"]["runtime_fixture"].update(
            source_eligibility_status="EXPIRED",
            bundle_eligibility_status="SOURCE_INELIGIBLE",
        )
        payload["expected"].update(
            expected_rule_outcome="NOT_INVOKED",
            expected_rule_ids=[],
            expected_rule_not_invoked_reason="BUNDLE_INELIGIBLE",
        )
    elif mutation == "not-invoked-with-dependency-fault":
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
    elif mutation == "matched-with-ineligible-inputs":
        payload["context"]["runtime_fixture"].update(
            source_eligibility_status="EXPIRED",
            bundle_eligibility_status="SOURCE_INELIGIBLE",
        )
    elif mutation in {"not-invoked-with-provider-invocation", "not-invoked-with-retrieval-invocation"}:
        payload["context"]["runtime_fixture"].update(
            source_eligibility_status="EXPIRED",
            bundle_eligibility_status="SOURCE_INELIGIBLE",
        )
        payload["expected"].update(
            expected_rule_outcome="NOT_INVOKED",
            expected_rule_ids=[],
            expected_rule_not_invoked_reason="SOURCE_INELIGIBLE",
            expected_provider_invocation=mutation == "not-invoked-with-provider-invocation",
            expected_retrieval_invocation=mutation == "not-invoked-with-retrieval-invocation",
        )
    else:
        payload["context"]["runtime_fixture"]["dependency_fault"] = "PROVIDER_TIMEOUT"

    schema = _json("schemas/1.1.0/authoring/rag-eval.case.schema.json")
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(payload))
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


@pytest.mark.parametrize(
    "fixture",
    ["rag-dev-answer-quality-001.json", "rag-dev-answer-grounding-001.json"],
)
def test_external_v1_1_answer_schema_rejects_rule_ids_without_rule_outcome(fixture: str) -> None:
    payload = _json(f"retrieval/cases/dev-foundation-v1/{fixture}")
    payload["schema_version"] = "1.1.0"
    payload["context"]["runtime_fixture"].update(
        source_eligibility_status="ELIGIBLE",
        bundle_eligibility_status="ELIGIBLE",
        dependency_fault="NONE",
    )
    payload["expected"].update(
        expected_rule_ids=["ev-synthetic-rule-001"],
        expected_rule_outcome=None,
        expected_rule_not_invoked_reason=None,
    )

    schema = _json("schemas/1.1.0/authoring/rag-eval.case.schema.json")
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(payload))
    with pytest.raises(ValidationError):
        EVALUATION_CASE_ADAPTER_V1_1.validate_python(payload)


@pytest.mark.parametrize(
    ("fixture_git_commit_sha", "protected_artifact_receipt_ref", "expected_valid"),
    [
        ("a" * 40, None, True),
        (None, "fixture", True),
        ("a" * 40, "fixture", False),
        (None, None, False),
    ],
    ids=("git-only", "protected-receipt-only", "both", "neither"),
)
def test_external_dataset_schema_matches_runtime_provenance_exactly_one(
    fixture_git_commit_sha: str | None,
    protected_artifact_receipt_ref: str | None,
    expected_valid: bool,
) -> None:
    payload = _json("retrieval/manifests/dev-foundation-v1.dataset.json")
    existing_receipt = payload["protected_artifact_receipt_ref"]
    payload["fixture_git_commit_sha"] = fixture_git_commit_sha
    payload["protected_artifact_receipt_ref"] = (
        existing_receipt if protected_artifact_receipt_ref == "fixture" else None
    )

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/authoring/rag-eval.dataset-manifest.schema.json",
        payload=payload,
        runtime_model=DatasetManifest,
        expected_valid=expected_valid,
    )


@pytest.mark.parametrize(
    ("target_kind", "runtime_ref", "fixture_ref", "expected_valid"),
    [
        ("FIXTURE_RECORD", False, True, True),
        ("RUNTIME_TYPED_REF", True, False, True),
        ("FIXTURE_RECORD", True, False, False),
        ("RUNTIME_TYPED_REF", False, True, False),
        ("FIXTURE_RECORD", True, True, False),
        ("RUNTIME_TYPED_REF", True, True, False),
        ("FIXTURE_RECORD", False, False, False),
        ("RUNTIME_TYPED_REF", False, False, False),
    ],
    ids=(
        "fixture-kind-fixture-only",
        "runtime-kind-runtime-only",
        "fixture-kind-mismatch",
        "runtime-kind-mismatch",
        "fixture-kind-both",
        "runtime-kind-both",
        "fixture-kind-neither",
        "runtime-kind-neither",
    ),
)
def test_external_evidence_schema_matches_runtime_target_branch_selection(
    target_kind: str,
    runtime_ref: bool,
    fixture_ref: bool,
    expected_valid: bool,
) -> None:
    payload = _json("retrieval/evidence/dev-foundation-v1.evidence-mapping.json")
    entry = deepcopy(payload["entries"][0])
    existing_fixture_ref = entry["fixture_record_ref"]
    entry["target_kind"] = target_kind
    entry["runtime_typed_ref"] = (
        {"id": "SYNTHETIC_RUNTIME_EVIDENCE", "version": "1.0.0", "hash": "b" * 64} if runtime_ref else None
    )
    entry["fixture_record_ref"] = existing_fixture_ref if fixture_ref else None
    payload["entries"][0] = entry

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/authoring/rag-eval.evidence-mapping-manifest.schema.json",
        payload=payload,
        runtime_model=EvidenceMappingManifest,
        expected_valid=expected_valid,
    )


def test_external_metric_schema_rejects_zero_denominator_pass() -> None:
    payload = _metric_payload()
    metric = payload["metrics"][0]
    metric.update(decision_status="PASS", metric_value=None, reason_code=None)

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.metrics.schema.json",
        payload=payload,
        runtime_model=MetricResults,
        expected_valid=False,
    )

    metric.update(decision_status="INCONCLUSIVE", reason_code="ZERO_DENOMINATOR")
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.metrics.schema.json",
        payload=payload,
        runtime_model=MetricResults,
        expected_valid=True,
    )


def test_external_gate_schema_rejects_completed_pass_for_empty_required_set() -> None:
    payload = _gate_payload()
    payload["required_metrics"] = []

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=payload,
        runtime_model=GateResult,
        expected_valid=False,
    )

    payload = _gate_payload()
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=payload,
        runtime_model=GateResult,
        expected_valid=True,
    )


def test_external_suite_schema_rejects_completed_pass_for_empty_cases() -> None:
    payload = _suite_payload()

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=False,
    )

    payload["case_results"] = [
        {
            "case_code": "case-001",
            "case_input_hash": "d" * 64,
            "execution_status": "COMPLETED",
            "decision_status": "PASS",
            "artifact_ref": {"id": "case-result", "version": "1.0.0", "hash": "e" * 64},
            "failure_code": None,
        }
    ]
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=True,
    )


@pytest.mark.parametrize(
    ("member_decision", "aggregate_decision"),
    [("FAIL", "FAIL"), ("INCONCLUSIVE", "INCONCLUSIVE")],
)
def test_external_gate_schema_enforces_completed_decision_precedence(
    member_decision: str,
    aggregate_decision: str,
) -> None:
    payload = _gate_payload()
    payload["required_metrics"][0]["decision_status"] = member_decision

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=payload,
        runtime_model=GateResult,
        expected_valid=False,
    )

    payload["aggregate_decision_status"] = aggregate_decision
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=payload,
        runtime_model=GateResult,
        expected_valid=True,
    )


def test_external_gate_schema_enforces_blocker_priority_and_exact_statuses() -> None:
    payload = _gate_payload()
    invalid_member = payload["required_metrics"][0]
    invalid_member.update(execution_status="INVALID", decision_status=None)
    error_member = deepcopy(invalid_member)
    error_member.update(member_type="CONTRACT_RECEIPT", member_id="runtime-contract", execution_status="ERROR")
    payload["required_contract_receipts"] = [error_member]

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=payload,
        runtime_model=GateResult,
        expected_valid=False,
    )

    payload.update(
        aggregate_execution_status="INVALID",
        aggregate_decision_status=None,
        blocking_execution_statuses=["INVALID", "ERROR"],
    )
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=payload,
        runtime_model=GateResult,
        expected_valid=True,
    )

    payload["blocking_execution_statuses"] = ["INVALID"]
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=payload,
        runtime_model=GateResult,
        expected_valid=False,
    )


def test_external_gate_schema_rejects_not_applicable_and_wrong_collection_type() -> None:
    not_applicable = _gate_payload()
    not_applicable["required_metrics"][0]["decision_status"] = "N/A"
    not_applicable["aggregate_decision_status"] = "N/A"
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=not_applicable,
        runtime_model=GateResult,
        expected_valid=False,
    )

    wrong_collection = _gate_payload()
    wrong_collection["required_metrics"][0]["member_type"] = "SUITE"
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.gate.schema.json",
        payload=wrong_collection,
        runtime_model=GateResult,
        expected_valid=False,
    )


def _suite_case(decision_status: str) -> dict[str, Any]:
    return {
        "case_code": "case-001",
        "case_input_hash": "d" * 64,
        "execution_status": "COMPLETED",
        "decision_status": decision_status,
        "artifact_ref": {"id": "case-result", "version": "1.0.0", "hash": "e" * 64},
        "failure_code": None,
    }


@pytest.mark.parametrize(
    ("case_decision", "required", "aggregate_decision"),
    [("FAIL", True, "FAIL"), ("INCONCLUSIVE", True, "INCONCLUSIVE"), ("N/A", False, "N/A")],
)
def test_external_suite_schema_enforces_completed_decision_precedence(
    case_decision: str,
    required: bool,
    aggregate_decision: str,
) -> None:
    payload = _suite_payload()
    payload.update(required=required, case_results=[_suite_case(case_decision)])

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=False,
    )

    payload["aggregate_decision_status"] = aggregate_decision
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=True,
    )


def test_external_suite_schema_enforces_blocker_priority_and_exact_statuses() -> None:
    payload = _suite_payload()
    invalid_case = _suite_case("PASS")
    invalid_case.update(execution_status="INVALID", decision_status=None)
    error_case = deepcopy(invalid_case)
    error_case.update(case_code="case-002", execution_status="ERROR")
    payload["case_results"] = [invalid_case, error_case]

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=False,
    )

    payload.update(
        aggregate_execution_status="INVALID",
        aggregate_decision_status=None,
        blocking_execution_statuses=["INVALID", "ERROR"],
    )
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=True,
    )

    payload["aggregate_execution_status"] = "ERROR"
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=False,
    )


def test_external_required_suite_schema_rejects_not_applicable_aggregate() -> None:
    payload = _suite_payload()
    payload.update(case_results=[_suite_case("N/A")], aggregate_decision_status="N/A")
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.suite-results.schema.json",
        payload=payload,
        runtime_model=SuiteResults,
        expected_valid=False,
    )


def test_external_comparison_schema_rejects_pass_for_mismatch_or_regression() -> None:
    mismatch = _comparison_payload()
    mismatch["controlled_variable_checks"][0]["matched"] = False
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.comparison.schema.json",
        payload=mismatch,
        runtime_model=ComparisonResult,
        expected_valid=False,
    )
    mismatch.update(execution_status="INVALID", decision_status=None)
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.comparison.schema.json",
        payload=mismatch,
        runtime_model=ComparisonResult,
        expected_valid=True,
    )

    regressed = _comparison_payload()
    regressed["scope_comparisons"][0]["comparison_decision"] = "REGRESSED"
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.comparison.schema.json",
        payload=regressed,
        runtime_model=ComparisonResult,
        expected_valid=False,
    )
    regressed["decision_status"] = "FAIL"
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.comparison.schema.json",
        payload=regressed,
        runtime_model=ComparisonResult,
        expected_valid=True,
    )


@pytest.mark.parametrize("empty_field", ["controlled_variable_checks", "scope_comparisons"])
def test_external_comparison_schema_rejects_completed_pass_for_empty_required_inputs(
    empty_field: str,
) -> None:
    payload = _comparison_payload()
    payload[empty_field] = []

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.comparison.schema.json",
        payload=payload,
        runtime_model=ComparisonResult,
        expected_valid=False,
    )

    payload.update(execution_status="INVALID", decision_status=None)
    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/artifacts/rag-eval.comparison.schema.json",
        payload=payload,
        runtime_model=ComparisonResult,
        expected_valid=True,
    )
