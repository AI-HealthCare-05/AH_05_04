from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.schemas import artifacts as artifact_schemas
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CASE_RESULT_ADAPTER,
    RESULT_ARTIFACT_MODELS,
    ContentManifest,
    FailureRecord,
    GateResult,
    MetricResults,
    RagEvaluationRun,
    ValidationReceipt,
)
from ai_worker.tasks.evaluation.schemas.common import SchemaValidationError

EXPECTED_ARTIFACT_IDS = {
    "rag-eval.run",
    "rag-eval.case-result",
    "rag-eval.metrics",
    "rag-eval.suite-results",
    "rag-eval.comparison",
    "rag-eval.gate",
    "rag-eval.failure",
    "rag-eval.content-manifest",
}
RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


def _ref(resource_id: str) -> dict[str, object]:
    return {
        "id": resource_id,
        "version": "1.0.0",
        "hash": "a" * 64,
    }


@pytest.fixture
def valid_run() -> dict[str, Any]:
    return {
        "schema_id": "rag-eval.run",
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "experiment_id": "experiment-001",
        "variant_id": "candidate-001",
        "experiment_type": "END_TO_END_RAG",
        "task_types": ["END_TO_END_RAG", "SAFETY"],
        "evaluation_profile_ref": _ref("profile.release"),
        "comparison_policy_ref": _ref("comparison.release"),
        "evaluation_policy_ref": _ref("policy.release"),
        "artifact_schema_set_ref": _ref("schema-set.release"),
        "dataset_code": "rag-foundation",
        "dataset_version": "1.0.0",
        "dataset_manifest_sha256": "b" * 64,
        "resource_set_hash": "c" * 64,
        "evidence_mapping_manifest_sha256": "d" * 64,
        "critical_claim_rubric_ref": _ref("rubric.release"),
        "fixture_git_commit_sha": "e" * 40,
        "protected_artifact_receipt_ref": None,
        "resolved_evaluation_config_hash": "f" * 64,
        "upstream_contract_manifest_hash": "1" * 64,
        "retrieval_variant_manifest_hash": "2" * 64,
        "answer_variant_manifest_hash": "3" * 64,
        "model_config_hash": "4" * 64,
        "prompt_version": "prompt-v1",
        "evaluated_partitions": ["HOLDOUT", "SAFETY_REGRESSION"],
        "partition_manifest_hash": "5" * 64,
        "environment": "LOCAL",
        "runtime_eligible": True,
        "candidate_bundle_id": "bundle-001",
        "candidate_bundle_manifest_hash": "6" * 64,
        "candidate_guard_decision_id": "guard-001",
        "candidate_guard_decision": "PASS",
        "required_case_guard_coverage_manifest_hash": "7" * 64,
        "executed_by": {
            "namespace": "GITHUB_LOGIN",
            "actor_id": "rag-owner",
            "role": "EVALUATION_IMPLEMENTER",
        },
        "started_at": "2026-09-01T00:00:00.000000Z",
        "completed_at": "2026-09-01T00:01:00.000000Z",
        "execution_status": "COMPLETED",
        "decision_status": "PASS",
        "blocking_execution_statuses": [],
        "result_content_manifest_hash": "8" * 64,
    }


def test_artifact_registry_contains_exactly_eight_ids() -> None:
    assert set(RESULT_ARTIFACT_MODELS) == EXPECTED_ARTIFACT_IDS


def test_incomplete_run_rejects_completion_fields(valid_run: dict[str, Any]) -> None:
    valid_run.update(
        execution_status="ERROR",
        decision_status="FAIL",
        result_content_manifest_hash="a" * 64,
    )

    with pytest.raises(ValidationError):
        RagEvaluationRun.model_validate(valid_run)


def test_runtime_eligible_run_requires_local_guard_bindings(valid_run: dict[str, Any]) -> None:
    RagEvaluationRun.model_validate(valid_run)
    for field in (
        "candidate_bundle_id",
        "candidate_bundle_manifest_hash",
        "candidate_guard_decision_id",
        "required_case_guard_coverage_manifest_hash",
    ):
        invalid = deepcopy(valid_run)
        invalid[field] = None
        with pytest.raises(ValidationError):
            RagEvaluationRun.model_validate(invalid)

    valid_run["environment"] = "CI"
    with pytest.raises(ValidationError):
        RagEvaluationRun.model_validate(valid_run)


def test_artifact_identifiers_reject_non_stable_ids(valid_run: dict[str, Any]) -> None:
    valid_run["experiment_id"] = "not a stable id"

    with pytest.raises(ValidationError):
        RagEvaluationRun.model_validate(valid_run)


def _retrieval_case_result() -> dict[str, Any]:
    return {
        "schema_id": "rag-eval.case-result",
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "case_id": "retrieval-001",
        "dataset_code": "rag-foundation",
        "dataset_version": "1.0.0",
        "task_type": "RETRIEVAL",
        "partition": "DEV",
        "input_sha256": "a" * 64,
        "execution_status": "COMPLETED",
        "decision_status": "PASS",
        "failure_codes": [],
        "retrieved_evidence_ids": ["evidence-001"],
        "selected_evidence_ids": ["evidence-001"],
        "actual_claim_ids": None,
        "actual_citation_evidence_ids": None,
        "actual_rule_ids": None,
        "actual_scope_codes": None,
        "actual_response_level": None,
        "actual_safety_disposition": None,
        "actual_execution_status": None,
        "actual_release_decision": None,
        "actual_fallback_code": None,
        "actual_provider_invocation": None,
        "actual_retrieval_invocation": True,
        "actual_publication_allowed": None,
        "actual_sections": None,
        "omitted_sections": None,
        "risk_level": None,
        "answer_sha256": None,
        "latency_ms": 12,
        "input_token_count": None,
        "output_token_count": None,
        "estimated_cost": None,
    }


def test_case_result_task_union_requires_explicit_retrieval_nullability() -> None:
    payload = _retrieval_case_result()
    result = CASE_RESULT_ADAPTER.validate_python(payload)
    assert result.task_type.value == "RETRIEVAL"

    payload["actual_claim_ids"] = []
    with pytest.raises(ValidationError):
        CASE_RESULT_ADAPTER.validate_python(payload)


def test_case_result_rejects_passed_boolean() -> None:
    payload = _retrieval_case_result()
    payload["passed"] = True

    with pytest.raises(ValidationError):
        CASE_RESULT_ADAPTER.validate_python(payload)


def _case_result_for_task(task_type: str) -> dict[str, Any]:
    payload = _retrieval_case_result()
    payload["task_type"] = task_type
    if task_type in {"ANSWER_QUALITY", "ANSWER_GROUNDING"}:
        payload.update(
            retrieved_evidence_ids=None,
            selected_evidence_ids=None,
            actual_claim_ids=["claim-001"],
            actual_citation_evidence_ids=["evidence-001"],
            actual_rule_ids=None,
            actual_scope_codes=None,
            actual_retrieval_invocation=None,
            answer_sha256="b" * 64,
            actual_sections=["SYNTHETIC_SECTION"],
            omitted_sections=[],
        )
    elif task_type in {"SAFETY", "END_TO_END_RAG"}:
        payload.update(
            retrieved_evidence_ids=["evidence-001"],
            selected_evidence_ids=["evidence-001"],
            actual_claim_ids=["claim-001"],
            actual_citation_evidence_ids=["evidence-001"],
            actual_rule_ids=["rule-001"],
            actual_scope_codes=["scope-001"],
            actual_response_level="URGENT",
            actual_safety_disposition="URGENT_ROUTED",
            actual_execution_status="SUCCEEDED",
            actual_release_decision="LIMITED",
            actual_fallback_code="SAFETY_ROUTED",
            actual_provider_invocation=False,
            actual_retrieval_invocation=True,
            actual_publication_allowed=True,
            answer_sha256="b" * 64,
            actual_sections=["SYNTHETIC_SAFETY_SECTION"],
            omitted_sections=[],
            risk_level="URGENT",
        )
    return payload


@pytest.mark.parametrize(
    "task_type",
    ["RETRIEVAL", "ANSWER_QUALITY", "ANSWER_GROUNDING", "SAFETY", "END_TO_END_RAG"],
)
def test_case_result_five_task_actual_section_and_risk_matrix_accepts_valid_payload(task_type: str) -> None:
    result = CASE_RESULT_ADAPTER.validate_python(_case_result_for_task(task_type))
    assert result.task_type.value == task_type


@pytest.mark.parametrize(
    ("task_type", "field", "invalid_value"),
    [
        ("RETRIEVAL", "actual_sections", []),
        ("ANSWER_QUALITY", "risk_level", "GENERAL"),
        ("ANSWER_GROUNDING", "omitted_sections", None),
        ("SAFETY", "actual_sections", None),
        ("END_TO_END_RAG", "risk_level", None),
    ],
)
def test_case_result_five_task_actual_section_and_risk_matrix_rejects_invalid_nullability(
    task_type: str,
    field: str,
    invalid_value: object,
) -> None:
    payload = _case_result_for_task(task_type)
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        CASE_RESULT_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("actual_response_level", "LOW"),
        ("actual_safety_disposition", "SAFE"),
        ("actual_fallback_code", "UNAPPROVED_FALLBACK"),
        ("risk_level", "LOW"),
    ],
)
def test_case_result_rejects_values_outside_authoritative_track_f_enums(
    field: str,
    invalid_value: str,
) -> None:
    payload = _case_result_for_task("SAFETY")
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        CASE_RESULT_ADAPTER.validate_python(payload)


def _metric_payload() -> dict[str, Any]:
    return {
        "schema_id": "rag-eval.metrics",
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "metrics": [
            {
                "metric_id": "citation-precision",
                "metric_version": "1.0.0",
                "partition": "HOLDOUT",
                "slice_id": "ALL",
                "required": True,
                "execution_status": "COMPLETED",
                "decision_status": "INCONCLUSIVE",
                "sample_case_count": 3,
                "sample_independent_group_count": 2,
                "numerator": 0,
                "denominator": 0,
                "metric_value": None,
                "unit_of_analysis": "CASE",
                "estimator_id": "proportion",
                "estimator_version": "1.0.0",
                "independence_unit": "CASE",
                "cluster_dimension": "question_template",
                "ci_lower": None,
                "ci_upper": None,
                "ci_method_id": "wilson",
                "ci_method_version": "1.0.0",
                "ci_level": "0.95",
                "ci_sidedness": "TWO_SIDED",
                "threshold": "0.95",
                "reason_code": "ZERO_DENOMINATOR",
            }
        ],
    }


def test_inconclusive_metric_requires_counts_denominator_and_reason() -> None:
    MetricResults.model_validate(_metric_payload())
    for field in ("sample_case_count", "sample_independent_group_count", "denominator", "reason_code"):
        payload = _metric_payload()
        payload["metrics"][0][field] = None
        with pytest.raises(ValidationError):
            MetricResults.model_validate(payload)


def test_incomplete_metric_rejects_calculated_values() -> None:
    payload = _metric_payload()
    metric = payload["metrics"][0]
    metric.update(execution_status="NOT_EVALUATED", decision_status=None)

    with pytest.raises(ValidationError):
        MetricResults.model_validate(payload)


def _gate_payload() -> dict[str, Any]:
    member = {
        "member_type": "METRIC",
        "member_id": "citation-precision",
        "member_version": "1.0.0",
        "member_hash": "b" * 64,
        "execution_status": "COMPLETED",
        "decision_status": "PASS",
        "receipt_or_artifact_ref": _ref("metric-artifact"),
    }
    return {
        "schema_id": "rag-eval.gate",
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "evaluation_policy_ref": _ref("policy.release"),
        "evaluation_profile_ref": _ref("profile.release"),
        "comparison_policy_ref": _ref("comparison.release"),
        "required_scope_manifest_hash": "a" * 64,
        "required_metrics": [member],
        "required_suites": [],
        "required_contract_receipts": [],
        "aggregate_execution_status": "COMPLETED",
        "aggregate_decision_status": "PASS",
        "blocking_execution_statuses": [],
        "blocking_reason_codes": [],
    }


def test_gate_rejects_aggregate_decision_when_required_member_is_incomplete() -> None:
    payload = _gate_payload()
    payload["required_metrics"][0].update(execution_status="ERROR", decision_status=None)

    with pytest.raises(ValidationError):
        GateResult.model_validate(payload)


def test_gate_blocking_statuses_are_derived_from_all_required_members() -> None:
    payload = _gate_payload()
    payload["required_metrics"][0].update(execution_status="ERROR", decision_status=None)
    payload.update(
        aggregate_execution_status="ERROR",
        aggregate_decision_status=None,
        blocking_execution_statuses=["ERROR"],
    )
    GateResult.model_validate(payload)

    payload["blocking_execution_statuses"] = []
    with pytest.raises(ValidationError):
        GateResult.model_validate(payload)


def test_gate_completed_decision_uses_fail_then_inconclusive_then_pass_precedence() -> None:
    payload = _gate_payload()
    failing_member = deepcopy(payload["required_metrics"][0])
    failing_member.update(member_id="critical-failure-count", decision_status="FAIL")
    payload["required_metrics"].append(failing_member)

    with pytest.raises(ValidationError):
        GateResult.model_validate(payload)

    payload["aggregate_decision_status"] = "FAIL"
    GateResult.model_validate(payload)


def test_gate_aggregate_execution_uses_exact_highest_blocker_priority() -> None:
    payload = _gate_payload()
    invalid_member = payload["required_metrics"][0]
    invalid_member.update(execution_status="INVALID", decision_status=None)
    error_member = deepcopy(invalid_member)
    error_member.update(member_type="CONTRACT_RECEIPT", member_id="runtime-contract", execution_status="ERROR")
    payload["required_contract_receipts"] = [error_member]
    payload.update(
        aggregate_execution_status="INVALID",
        aggregate_decision_status=None,
        blocking_execution_statuses=["INVALID", "ERROR"],
    )
    GateResult.model_validate(payload)

    payload["aggregate_execution_status"] = "ERROR"
    with pytest.raises(ValidationError):
        GateResult.model_validate(payload)


def test_failure_summary_is_short_and_non_sensitive() -> None:
    payload = {
        "schema_id": "rag-eval.failure",
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "case_id": "safety-001",
        "failure_code": "SAFETY_SCOPE_MISMATCH",
        "failure_stage": "ASSERTION",
        "expected_summary": "EXPECTED_APPROVED_SYNTHETIC_SAFETY_ROUTE",
        "actual_summary": "ACTUAL_NON_SENSITIVE_ROUTE_MISMATCH",
        "root_cause_code": None,
        "followup_issue_ref": None,
        "created_at": "2026-09-01T00:01:00.000000Z",
    }
    FailureRecord.model_validate(payload)

    payload["actual_summary"] = "patient@example.com"
    with pytest.raises((ValidationError, ValueError)):
        FailureRecord.model_validate(payload)

    payload["actual_summary"] = "x" * 501
    with pytest.raises(ValidationError):
        FailureRecord.model_validate(payload)


def test_failure_summary_rejects_uncatalogued_caller_free_text() -> None:
    payload = {
        "schema_id": "rag-eval.failure",
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "case_id": "safety-001",
        "failure_code": "SAFETY_SCOPE_MISMATCH",
        "failure_stage": "ASSERTION",
        "expected_summary": "This harmless-looking text may still contain a caller query",
        "actual_summary": "This harmless-looking text may still contain a provider body",
        "root_cause_code": None,
        "followup_issue_ref": None,
        "created_at": "2026-09-01T00:01:00.000000Z",
    }

    with pytest.raises(ValidationError):
        FailureRecord.model_validate(payload)


def test_content_manifest_requires_sorted_allowed_non_self_entries_and_count() -> None:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.content-manifest",
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "hash_algorithm": "SHA-256",
        "artifacts": [
            {"relative_path": "cases.jsonl", "sha256": "a" * 64, "size_bytes": 1},
            {"relative_path": "metrics.json", "sha256": "b" * 64, "size_bytes": 2},
        ],
        "artifact_count": 2,
        "manifest_sha256": "2a147f775f9328bfa99844e82887bfca6b58e3a813f1ee4c091f3977ec1b3dbf",
    }
    ContentManifest.model_validate(payload)

    for artifacts in (
        list(reversed(payload["artifacts"])),
        [{"relative_path": "run.json", "sha256": "a" * 64, "size_bytes": 1}],
        [
            {
                "relative_path": "result-content-manifest.json",
                "sha256": "a" * 64,
                "size_bytes": 1,
            }
        ],
    ):
        invalid = deepcopy(payload)
        invalid["artifacts"] = artifacts
        invalid["artifact_count"] = len(artifacts)
        with pytest.raises(ValidationError):
            ContentManifest.model_validate(invalid)

    payload["artifact_count"] = 1
    with pytest.raises(ValidationError):
        ContentManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("execution_status", "decision_status"),
    [("COMPLETED", "N/A"), ("INVALID", None), ("ERROR", None)],
)
def test_validation_receipt_accepts_only_validation_outcomes(
    execution_status: str,
    decision_status: str | None,
) -> None:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.validation-receipt",
        "schema_version": "1.0.0",
        "validation_id": RUN_ID,
        "validated_at": "2026-09-01T00:01:00.000000Z",
        "validator_version": "1.0.0",
        "manifest_path": "datasets/manifest.json",
        "dataset_code": "rag-foundation",
        "dataset_version": "1.0.0",
        "dataset_manifest_sha256": "a" * 64,
        "evaluation_profile_ref": _ref("profile.release"),
        "comparison_policy_ref": _ref("comparison.release"),
        "execution_status": execution_status,
        "decision_status": decision_status,
        "release_eligible": False,
        "error_codes": [],
        "invalid_resource_paths": [],
    }
    receipt = ValidationReceipt.model_validate(payload)
    assert "run_id" not in receipt.model_dump()

    payload["release_eligible"] = True
    with pytest.raises(ValidationError):
        ValidationReceipt.model_validate(payload)


def test_validation_receipt_rejects_evaluation_run_outcomes() -> None:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.validation-receipt",
        "schema_version": "1.0.0",
        "validation_id": RUN_ID,
        "validated_at": "2026-09-01T00:01:00.000000Z",
        "validator_version": "1.0.0",
        "manifest_path": "datasets/manifest.json",
        "dataset_code": "rag-foundation",
        "dataset_version": "1.0.0",
        "dataset_manifest_sha256": None,
        "evaluation_profile_ref": None,
        "comparison_policy_ref": None,
        "execution_status": "COMPLETED",
        "decision_status": "PASS",
        "release_eligible": False,
        "error_codes": [],
        "invalid_resource_paths": [],
    }
    with pytest.raises(ValidationError):
        ValidationReceipt.model_validate(payload)


@pytest.mark.parametrize("sensitive_path", ["invalid/patient@example.com", "invalid/010-1234-5678.json"])
def test_validation_receipt_rejects_sensitive_paths_without_echoing_values(sensitive_path: str) -> None:
    payload = {
        "schema_id": "rag-eval.validation-receipt",
        "schema_version": "1.0.0",
        "validation_id": RUN_ID,
        "validated_at": "2026-09-01T00:01:00.000000Z",
        "validator_version": "1.0.0",
        "manifest_path": "datasets/manifest.json",
        "dataset_code": "rag-foundation",
        "dataset_version": "1.0.0",
        "dataset_manifest_sha256": None,
        "evaluation_profile_ref": None,
        "comparison_policy_ref": None,
        "execution_status": "INVALID",
        "decision_status": None,
        "release_eligible": False,
        "error_codes": ["EVAL_SCHEMA_INVALID"],
        "invalid_resource_paths": [sensitive_path],
    }

    with pytest.raises(ValidationError) as caught:
        ValidationReceipt.model_validate(payload)

    assert sensitive_path not in str(caught.value)


@pytest.mark.parametrize(
    "sensitive_path",
    [
        "invalid/patient@example.com",
        "invalid/010-1234-5678",
        "invalid/sk-proj-abcdefghijklmnop",
    ],
)
def test_public_validation_receipt_error_serialization_never_contains_sensitive_input(
    sensitive_path: str,
) -> None:
    validate_validation_receipt = getattr(artifact_schemas, "validate_validation_receipt", None)
    assert callable(validate_validation_receipt)
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.validation-receipt",
        "schema_version": "1.0.0",
        "validation_id": RUN_ID,
        "validated_at": "2026-09-01T00:01:00.000000Z",
        "validator_version": "1.0.0",
        "manifest_path": "datasets/manifest.json",
        "dataset_code": "rag-foundation",
        "dataset_version": "1.0.0",
        "dataset_manifest_sha256": None,
        "evaluation_profile_ref": None,
        "comparison_policy_ref": None,
        "execution_status": "INVALID",
        "decision_status": None,
        "release_eligible": False,
        "error_codes": ["EVAL_SCHEMA_INVALID"],
        "invalid_resource_paths": [sensitive_path],
    }

    with pytest.raises(SchemaValidationError) as caught:
        validate_validation_receipt(payload)

    assert sensitive_path not in repr(caught.value.errors())
    assert sensitive_path not in caught.value.json()
