from __future__ import annotations

from dataclasses import replace

from ai_worker.tasks.evaluation.release_gate import (
    GateEvidence,
    MetricEvidence,
    MetricRequirement,
    PairedCaseEvidence,
    ReceiptEvidence,
    ReleaseGatePolicy,
    SuiteEvidence,
    build_release_gate,
    release_gate_exit_code,
)
from ai_worker.tasks.evaluation.schemas.artifacts import MetricResult, SuiteResults
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
    Partition,
)

RUN_ID = "11111111-1111-4111-8111-111111111111"


def _ref(identifier: str, value: str) -> ImmutableReference:
    return ImmutableReference(id=identifier, version="1.0.0", hash=value * 64)


def _policy() -> ReleaseGatePolicy:
    return ReleaseGatePolicy(
        evaluation_policy_ref=_ref("release-policy", "a"),
        evaluation_profile_ref=_ref("release-profile", "b"),
        comparison_policy_ref=_ref("release-comparison", "c"),
        runtime_eligible=True,
        required_experiment_types=(ExperimentType.END_TO_END_RAG,),
        required_partitions=(Partition.HOLDOUT, Partition.SAFETY_REGRESSION),
        required_metrics=(),
        required_suites=(),
        required_receipts=(
            _ref("baseline-freeze-receipt", "d"),
            _ref("ans-base-to-ans-final-comparison", "e"),
        ),
    )


def _paired() -> PairedCaseEvidence:
    return PairedCaseEvidence(
        receipt_id="ans-base-to-ans-final-comparison",
        required_case_ids=("case-001", "case-002"),
        baseline_case_ids=("case-001", "case-002"),
        candidate_case_ids=("case-001", "case-002"),
        final_case_ids=("case-001", "case-002"),
        required_control_keys=("MODEL", "SEED", "TEMPERATURE"),
        baseline_control_hashes=("1" * 64, "2" * 64, "3" * 64),
        candidate_control_hashes=("1" * 64, "2" * 64, "3" * 64),
        final_control_hashes=("1" * 64, "2" * 64, "3" * 64),
        paired_delta_complete=True,
    )


def _receipt(identifier: str, value: str) -> ReceiptEvidence:
    reference = _ref(identifier, value)
    return ReceiptEvidence(
        reference=reference,
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=DecisionStatus.PASS,
        artifact_ref=reference,
    )


def _evidence() -> GateEvidence:
    return GateEvidence(
        run_id=RUN_ID,
        required_scope_manifest_hash="f" * 64,
        completed_experiment_types=(ExperimentType.END_TO_END_RAG,),
        completed_partitions=(Partition.HOLDOUT, Partition.SAFETY_REGRESSION),
        metrics=(),
        suites=(),
        receipts=(
            _receipt("baseline-freeze-receipt", "d"),
            _receipt("ans-base-to-ans-final-comparison", "e"),
        ),
        paired_case_evidence=_paired(),
    )


def test_all_required_synthetic_evidence_produces_pass() -> None:
    gate = build_release_gate(_policy(), _evidence())

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.PASS
    assert gate.blocking_reason_codes == ()


def test_missing_baseline_receipt_is_not_evaluated_and_never_passes() -> None:
    evidence = _evidence()
    evidence = replace(
        evidence,
        receipts=tuple(item for item in evidence.receipts if item.reference.id != "baseline-freeze-receipt"),
    )

    gate = build_release_gate(_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.NOT_EVALUATED
    assert gate.aggregate_decision_status is None
    assert "BASELINE_FREEZE_RECEIPT_MISSING" in gate.blocking_reason_codes


def test_baseline_receipt_hash_mismatch_is_invalid_and_never_passes() -> None:
    evidence = _evidence()
    mismatched = _receipt("baseline-freeze-receipt", "9")
    evidence = replace(evidence, receipts=(mismatched, evidence.receipts[1]))

    gate = build_release_gate(_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "BASELINE_FREEZE_RECEIPT_HASH_MISMATCH" in gate.blocking_reason_codes


def test_duplicate_receipt_identity_is_invalid_independent_of_input_order() -> None:
    evidence = _evidence()
    duplicate = _receipt("baseline-freeze-receipt", "d")

    first = build_release_gate(
        _policy(),
        replace(evidence, receipts=(duplicate, *evidence.receipts)),
    )
    last = build_release_gate(
        _policy(),
        replace(evidence, receipts=(*evidence.receipts, duplicate)),
    )

    assert first.aggregate_execution_status is ExecutionStatus.INVALID
    assert last.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_RECEIPT_DUPLICATE:baseline-freeze-receipt" in first.blocking_reason_codes
    assert first.blocking_reason_codes == last.blocking_reason_codes


def test_unpaired_required_case_is_invalid_and_never_passes() -> None:
    evidence = _evidence()
    paired = replace(evidence.paired_case_evidence, final_case_ids=("case-001",))
    evidence = replace(evidence, paired_case_evidence=paired)

    gate = build_release_gate(_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "PAIRED_CASE_SET_MISMATCH" in gate.blocking_reason_codes


def test_paired_case_coverage_compares_sets_not_input_order() -> None:
    evidence = _evidence()
    paired = replace(evidence.paired_case_evidence, final_case_ids=("case-002", "case-001"))

    gate = build_release_gate(_policy(), replace(evidence, paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.PASS


def test_duplicate_control_key_is_invalid_and_never_passes() -> None:
    evidence = _evidence()
    paired = replace(
        evidence.paired_case_evidence,
        required_control_keys=("MODEL", "MODEL", "TEMPERATURE"),
    )

    gate = build_release_gate(_policy(), replace(evidence, paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "PAIRED_CONTROL_SETTING_MISMATCH" in gate.blocking_reason_codes


def test_control_setting_mismatch_is_invalid_and_never_passes() -> None:
    evidence = _evidence()
    paired = replace(
        evidence.paired_case_evidence,
        final_control_hashes=("1" * 64, "8" * 64, "3" * 64),
    )

    gate = build_release_gate(_policy(), replace(evidence, paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "PAIRED_CONTROL_SETTING_MISMATCH" in gate.blocking_reason_codes


def test_missing_paired_delta_is_completed_inconclusive() -> None:
    evidence = _evidence()
    paired = replace(evidence.paired_case_evidence, paired_delta_complete=False)

    gate = build_release_gate(_policy(), replace(evidence, paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.INCONCLUSIVE
    assert "PAIRED_DELTA_OR_CI_MISSING" in gate.blocking_reason_codes


def test_critical_receipt_failure_overrides_other_completed_results() -> None:
    evidence = _evidence()
    critical = replace(
        evidence.receipts[0],
        decision_status=DecisionStatus.FAIL,
    )

    gate = build_release_gate(_policy(), replace(evidence, receipts=(critical, evidence.receipts[1])))

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.FAIL


def test_gate_exit_codes_distinguish_pass_fail_and_inconclusive() -> None:
    passed = build_release_gate(_policy(), _evidence())
    failed_receipt = replace(_evidence().receipts[0], decision_status=DecisionStatus.FAIL)
    failed = build_release_gate(
        _policy(),
        replace(_evidence(), receipts=(failed_receipt, _evidence().receipts[1])),
    )
    inconclusive = build_release_gate(
        _policy(),
        replace(
            _evidence(),
            paired_case_evidence=replace(_paired(), paired_delta_complete=False),
        ),
    )

    assert release_gate_exit_code(passed) == 0
    assert release_gate_exit_code(failed) == 1
    assert release_gate_exit_code(inconclusive) == 2


def _required_metric(decision: str = "PASS", denominator: int = 2) -> MetricResult:
    return MetricResult.model_validate(
        {
            "metric_id": "CRITICAL_SAFETY_FAILURE_COUNT",
            "metric_version": "1.0.0",
            "partition": "HOLDOUT",
            "slice_id": "ALL",
            "required": True,
            "execution_status": "COMPLETED",
            "decision_status": decision,
            "sample_case_count": 2,
            "sample_independent_group_count": 2,
            "numerator": 0,
            "denominator": denominator,
            "metric_value": None if denominator == 0 else "0",
            "unit_of_analysis": "CASE",
            "estimator_id": "COUNT",
            "estimator_version": "1.0.0",
            "independence_unit": "CASE",
            "cluster_dimension": None,
            "ci_lower": None,
            "ci_upper": None,
            "ci_method_id": None,
            "ci_method_version": None,
            "ci_level": None,
            "ci_sidedness": None,
            "threshold": "0",
            "reason_code": "ZERO_DENOMINATOR" if denominator == 0 else None,
        }
    )


def _metric_policy() -> ReleaseGatePolicy:
    return replace(
        _policy(),
        required_metrics=(
            MetricRequirement(
                metric_id="CRITICAL_SAFETY_FAILURE_COUNT",
                metric_version="1.0.0",
                partition=Partition.HOLDOUT,
                slice_id="ALL",
                requirement_hash="4" * 64,
            ),
        ),
    )


def test_required_metric_missing_is_not_evaluated() -> None:
    gate = build_release_gate(_metric_policy(), _evidence())

    assert gate.aggregate_execution_status is ExecutionStatus.NOT_EVALUATED
    assert gate.aggregate_decision_status is None
    assert "REQUIRED_METRIC_MISSING:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def test_required_metric_zero_denominator_is_completed_inconclusive() -> None:
    metric = _required_metric(decision="INCONCLUSIVE", denominator=0)
    evidence = replace(
        _evidence(),
        metrics=(MetricEvidence(metric=metric, artifact_ref=_ref("critical-safety-metric", "5")),),
    )

    gate = build_release_gate(_metric_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.INCONCLUSIVE


def test_duplicate_required_metric_identity_is_invalid() -> None:
    metric = MetricEvidence(
        metric=_required_metric(),
        artifact_ref=_ref("critical-safety-metric", "5"),
    )

    gate = build_release_gate(_metric_policy(), replace(_evidence(), metrics=(metric, metric)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_METRIC_DUPLICATE:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def _suite_evidence(value: str) -> SuiteEvidence:
    suite = SuiteResults.model_validate(
        {
            "schema_id": "rag-eval.suite-results",
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "suite_id": "required-suite",
            "suite_version": "1.0.0",
            "suite_definition_hash": "6" * 64,
            "required": True,
            "expected_case_set_hash": "7" * 64,
            "executed_case_set_hash": "7" * 64,
            "case_results": [
                {
                    "case_code": "case-001",
                    "case_input_hash": "8" * 64,
                    "execution_status": "COMPLETED",
                    "decision_status": "PASS",
                    "artifact_ref": _ref("suite-case", "9").model_dump(mode="json"),
                    "failure_code": None,
                }
            ],
            "aggregate_execution_status": "COMPLETED",
            "aggregate_decision_status": "PASS",
            "blocking_execution_statuses": [],
            "artifact_hash": value * 64,
        }
    )
    return SuiteEvidence(suite=suite, artifact_ref=_ref("required-suite", value))


def test_required_suite_hash_mismatch_is_invalid() -> None:
    policy = replace(_policy(), required_suites=(_ref("required-suite", "6"),))
    evidence = replace(_evidence(), suites=(_suite_evidence("7"),))

    gate = build_release_gate(policy, evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "REQUIRED_SUITE_HASH_MISMATCH:required-suite" in gate.blocking_reason_codes


def test_duplicate_required_suite_identity_is_invalid() -> None:
    policy = replace(_policy(), required_suites=(_ref("required-suite", "7"),))
    suite = _suite_evidence("7")

    gate = build_release_gate(policy, replace(_evidence(), suites=(suite, suite)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_SUITE_DUPLICATE:required-suite" in gate.blocking_reason_codes
