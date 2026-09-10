from __future__ import annotations

from dataclasses import replace

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.release_gate import (
    ControlSettingEvidence,
    GateEvidence,
    MetricEvidence,
    MetricRequirement,
    PairedCaseEvidence,
    ReceiptEvidence,
    ReleaseGatePolicy,
    SuiteEvidence,
    build_release_gate,
    paired_case_manifest_hash,
    release_gate_exit_code,
)
from ai_worker.tasks.evaluation.schemas.artifacts import MetricResult, MetricResults, SuiteResults
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
    Partition,
)
from ai_worker.tasks.evaluation.schemas.policy import SuiteDefinition

RUN_ID = "11111111-1111-4111-8111-111111111111"


def _ref(identifier: str, value: str) -> ImmutableReference:
    return ImmutableReference(id=identifier, version="1.0.0", hash=value * 64)


def _policy() -> ReleaseGatePolicy:
    paired_hash = _paired().receipt_hash
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
            ImmutableReference(
                id="ans-base-to-ans-final-comparison",
                version="1.0.0",
                hash=paired_hash,
            ),
        ),
        paired_comparison_receipt_id="ans-base-to-ans-final-comparison",
        required_case_ids=("case-001", "case-002"),
        controlled_variable_keys=("MODEL", "SEED", "TEMPERATURE"),
        required_scope_manifest_hash="f" * 64,
    )


def _paired() -> PairedCaseEvidence:
    evidence = PairedCaseEvidence(
        receipt_id="ans-base-to-ans-final-comparison",
        receipt_hash="0" * 64,
        baseline_case_ids=("case-001", "case-002"),
        candidate_case_ids=("case-001", "case-002"),
        final_case_ids=("case-001", "case-002"),
        control_settings=(
            ControlSettingEvidence("MODEL", "1" * 64, "1" * 64, "1" * 64),
            ControlSettingEvidence("SEED", "2" * 64, "2" * 64, "2" * 64),
            ControlSettingEvidence("TEMPERATURE", "3" * 64, "3" * 64, "3" * 64),
        ),
        paired_delta_refs=(_ref("paired-delta-and-ci", "4"),),
    )
    return replace(evidence, receipt_hash=paired_case_manifest_hash(evidence))


def _receipt(identifier: str, value: str) -> ReceiptEvidence:
    reference = _ref(identifier, value)
    return ReceiptEvidence(
        reference=reference,
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=DecisionStatus.PASS,
        artifact_ref=reference,
        is_current=True,
    )


def _paired_receipt() -> ReceiptEvidence:
    reference = ImmutableReference(
        id="ans-base-to-ans-final-comparison",
        version="1.0.0",
        hash=_paired().receipt_hash,
    )
    return ReceiptEvidence(
        reference=reference,
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=DecisionStatus.PASS,
        artifact_ref=reference,
        is_current=True,
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
            _paired_receipt(),
        ),
        paired_case_evidence=_paired(),
    )


def test_all_required_synthetic_evidence_produces_pass() -> None:
    gate = build_release_gate(_policy(), _evidence())

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.PASS
    assert gate.blocking_reason_codes == ()


def test_missing_paired_evidence_never_passes_when_comparison_receipt_is_required() -> None:
    gate = build_release_gate(_policy(), replace(_evidence(), paired_case_evidence=None))

    assert gate.aggregate_decision_status is not DecisionStatus.PASS
    assert "PAIRED_COMPARISON_EVIDENCE_MISSING" in gate.blocking_reason_codes


def test_paired_evidence_for_wrong_receipt_never_passes() -> None:
    paired = replace(_paired(), receipt_id="unrelated-comparison")

    gate = build_release_gate(_policy(), replace(_evidence(), paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None


def test_paired_evidence_hash_must_match_comparison_receipt() -> None:
    paired = replace(_paired(), receipt_hash="0" * 64)

    gate = build_release_gate(_policy(), replace(_evidence(), paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "PAIRED_COMPARISON_HASH_MISMATCH" in gate.blocking_reason_codes


def test_empty_paired_case_and_control_sets_never_pass() -> None:
    paired = replace(
        _paired(),
        baseline_case_ids=(),
        candidate_case_ids=(),
        final_case_ids=(),
        control_settings=(),
    )

    gate = build_release_gate(_policy(), replace(_evidence(), paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None


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


def test_required_scope_manifest_hash_mismatch_is_invalid() -> None:
    evidence = replace(_evidence(), required_scope_manifest_hash="0" * 64)

    gate = build_release_gate(_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_SCOPE_MANIFEST_HASH_MISMATCH" in gate.blocking_reason_codes


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
    paired = replace(_paired(), final_case_ids=("case-001",))
    evidence = replace(evidence, paired_case_evidence=paired)

    gate = build_release_gate(_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "PAIRED_CASE_SET_MISMATCH" in gate.blocking_reason_codes


def test_paired_case_scope_cannot_be_shrunk_by_altering_policy_and_evidence_together() -> None:
    policy = replace(_policy(), required_case_ids=("case-001",))
    paired = replace(
        _paired(),
        baseline_case_ids=("case-001",),
        candidate_case_ids=("case-001",),
        final_case_ids=("case-001",),
    )

    gate = build_release_gate(policy, replace(_evidence(), paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "PAIRED_COMPARISON_CONTENT_HASH_MISMATCH" in gate.blocking_reason_codes


def test_paired_case_coverage_compares_sets_not_input_order() -> None:
    evidence = _evidence()
    paired = replace(_paired(), final_case_ids=("case-002", "case-001"))

    gate = build_release_gate(_policy(), replace(evidence, paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.PASS


def test_duplicate_control_key_is_invalid_and_never_passes() -> None:
    evidence = _evidence()
    paired = replace(
        _paired(),
        control_settings=(
            ControlSettingEvidence("MODEL", "1" * 64, "1" * 64, "1" * 64),
            ControlSettingEvidence("MODEL", "2" * 64, "2" * 64, "2" * 64),
            ControlSettingEvidence("TEMPERATURE", "3" * 64, "3" * 64, "3" * 64),
        ),
    )

    gate = build_release_gate(_policy(), replace(evidence, paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "PAIRED_CONTROL_SETTING_MISMATCH" in gate.blocking_reason_codes


def test_control_setting_mismatch_is_invalid_and_never_passes() -> None:
    evidence = _evidence()
    paired = replace(
        _paired(),
        control_settings=(
            ControlSettingEvidence("MODEL", "1" * 64, "1" * 64, "1" * 64),
            ControlSettingEvidence("SEED", "2" * 64, "2" * 64, "8" * 64),
            ControlSettingEvidence("TEMPERATURE", "3" * 64, "3" * 64, "3" * 64),
        ),
    )

    gate = build_release_gate(_policy(), replace(evidence, paired_case_evidence=paired))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "PAIRED_CONTROL_SETTING_MISMATCH" in gate.blocking_reason_codes


def test_missing_paired_delta_is_completed_inconclusive() -> None:
    evidence = _evidence()
    paired = replace(_paired(), paired_delta_refs=())

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
            paired_case_evidence=replace(_paired(), paired_delta_refs=()),
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
            "ci_lower": None if denominator == 0 else "0",
            "ci_upper": None if denominator == 0 else "0",
            "ci_method_id": "WILSON_SCORE",
            "ci_method_version": "1.0.0",
            "ci_level": "0.95",
            "ci_sidedness": "TWO_SIDED",
            "threshold": "0",
            "reason_code": "ZERO_DENOMINATOR" if denominator == 0 else None,
        }
    )


def _metric_evidence(
    metric: MetricResult,
    *,
    run_id: str = RUN_ID,
    artifact_hash: str | None = None,
) -> MetricEvidence:
    artifact = MetricResults(
        schema_id="rag-eval.metrics",
        schema_version="1.0.0",
        run_id=run_id,
        metrics=(metric,),
    )
    digest = canonical_sha256(artifact.model_dump(mode="json"))
    return MetricEvidence(
        metric=metric,
        artifact=artifact,
        artifact_ref=ImmutableReference(
            id="critical-safety-metric",
            version="1.0.0",
            hash=artifact_hash or digest,
        ),
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
                unit_of_analysis="CASE",
                estimator_id="COUNT",
                estimator_version="1.0.0",
                minimum_case_count=1,
                independence_unit="CASE",
                cluster_dimension=None,
                minimum_independent_group_count=None,
                threshold="0",
                decision_basis="AT_MOST",
                ci_method_id="WILSON_SCORE",
                ci_method_version="1.0.0",
                ci_level="0.95",
                ci_sidedness="TWO_SIDED",
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
        metrics=(_metric_evidence(metric),),
    )

    gate = build_release_gate(_metric_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.INCONCLUSIVE


def test_required_metric_cannot_pass_with_non_required_artifact_metadata() -> None:
    metric = _required_metric().model_copy(update={"required": False})
    evidence = replace(
        _evidence(),
        metrics=(_metric_evidence(metric),),
    )

    gate = build_release_gate(_metric_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "REQUIRED_METRIC_POLICY_MISMATCH:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def test_required_metric_decision_is_checked_against_threshold() -> None:
    metric = _required_metric().model_copy(
        update={
            "numerator": 1,
            "metric_value": "1",
            "ci_lower": "1",
            "ci_upper": "1",
            "decision_status": DecisionStatus.PASS,
        }
    )
    evidence = replace(
        _evidence(),
        metrics=(_metric_evidence(metric),),
    )

    gate = build_release_gate(_metric_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_METRIC_DECISION_MISMATCH:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def test_required_metric_missing_ci_is_completed_inconclusive() -> None:
    metric = _required_metric().model_copy(update={"ci_lower": None, "ci_upper": None})
    evidence = replace(
        _evidence(),
        metrics=(_metric_evidence(metric),),
    )

    gate = build_release_gate(_metric_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.INCONCLUSIVE


def test_duplicate_required_metric_identity_is_invalid() -> None:
    metric = _metric_evidence(_required_metric())

    gate = build_release_gate(_metric_policy(), replace(_evidence(), metrics=(metric, metric)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_METRIC_DUPLICATE:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def test_required_metric_value_must_match_counts() -> None:
    metric = _required_metric().model_copy(update={"numerator": 1, "metric_value": "0"})
    evidence = replace(
        _evidence(),
        metrics=(_metric_evidence(metric),),
    )

    gate = build_release_gate(_metric_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_METRIC_VALUE_MISMATCH:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def test_case_mean_uses_canonical_artifact_without_pooled_ratio_false_blocking() -> None:
    for numerator, denominator, value, lower, upper in (
        (2, 3, "0.75", "0.7", "0.8"),
        (1, 3, "0.333333", "0.3", "0.4"),
    ):
        metric = _required_metric().model_copy(
            update={
                "estimator_id": "CASE_MEAN",
                "numerator": numerator,
                "denominator": denominator,
                "metric_value": value,
                "ci_lower": lower,
                "ci_upper": upper,
                "threshold": "1",
            }
        )
        requirement = replace(
            _metric_policy().required_metrics[0],
            estimator_id="CASE_MEAN",
            threshold="1",
        )

        gate = build_release_gate(
            replace(_metric_policy(), required_metrics=(requirement,)),
            replace(_evidence(), metrics=(_metric_evidence(metric),)),
        )

        assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
        assert gate.aggregate_decision_status is DecisionStatus.PASS


def test_ratio_metric_accepts_canonical_six_place_rounding() -> None:
    metric = _required_metric().model_copy(
        update={
            "estimator_id": "PROPORTION",
            "numerator": 1,
            "denominator": 3,
            "metric_value": "0.333333",
            "ci_lower": "0.3",
            "ci_upper": "0.4",
            "threshold": "1",
        }
    )
    requirement = replace(
        _metric_policy().required_metrics[0],
        estimator_id="PROPORTION",
        threshold="1",
    )

    gate = build_release_gate(
        replace(_metric_policy(), required_metrics=(requirement,)),
        replace(_evidence(), metrics=(_metric_evidence(metric),)),
    )

    assert gate.aggregate_execution_status is ExecutionStatus.COMPLETED
    assert gate.aggregate_decision_status is DecisionStatus.PASS


def test_required_metric_rejects_invalid_ci_bounds() -> None:
    metric = _required_metric().model_copy(update={"ci_lower": "0.1", "ci_upper": "0"})
    evidence = replace(
        _evidence(),
        metrics=(_metric_evidence(metric),),
    )

    gate = build_release_gate(_metric_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_METRIC_CI_INVALID:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def test_required_metric_must_match_gate_run_and_artifact_hash() -> None:
    metric = _metric_evidence(
        _required_metric(),
        run_id="22222222-2222-4222-8222-222222222222",
        artifact_hash="0" * 64,
    )

    gate = build_release_gate(_metric_policy(), replace(_evidence(), metrics=(metric,)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_METRIC_ARTIFACT_MISMATCH:CRITICAL_SAFETY_FAILURE_COUNT:HOLDOUT:ALL" in gate.blocking_reason_codes


def test_unsupported_required_metric_decision_basis_is_not_implemented() -> None:
    policy = _metric_policy()
    requirement = replace(policy.required_metrics[0], decision_basis="NON_INFERIORITY")
    metric = _metric_evidence(_required_metric())

    gate = build_release_gate(
        replace(policy, required_metrics=(requirement,)),
        replace(_evidence(), metrics=(metric,)),
    )

    assert gate.aggregate_execution_status is ExecutionStatus.NOT_IMPLEMENTED
    assert gate.aggregate_decision_status is None


def _suite_evidence() -> SuiteEvidence:
    case_set_hash = canonical_sha256({"case_ids": ["case-001"]})
    definition = SuiteDefinition.model_validate(
        {
            "schema_id": "rag-eval.suite-definition",
            "schema_version": "1.0.0",
            "suite_id": "required-suite",
            "suite_version": "1.0.0",
            "suite_hash": "0" * 64,
            "adapter_id": "synthetic-gate-test",
            "command": ["synthetic-noop"],
            "input_selector": {
                "dataset_code": "synthetic-release-gate",
                "dataset_version": "1.0.0",
                "partitions": ["HOLDOUT"],
                "task_types": ["END_TO_END_RAG"],
            },
            "expected_case_set_hash": case_set_hash,
            "critical_invariant_ids": ["SYNTHETIC_REQUIRED_CASE_COVERAGE"],
            "pass_rule": "ALL_REQUIRED_CASES_PASS",
            "artifact_contract_version": "1.0.0",
            "required": True,
            "review_provenance": {
                "authored_by": {
                    "namespace": "GITHUB_LOGIN",
                    "actor_id": "synthetic-author",
                    "role": "EVALUATION_IMPLEMENTER",
                },
                "authored_at": "2026-09-10T00:00:00.000000Z",
                "reviewed_by": {
                    "namespace": "GITHUB_LOGIN",
                    "actor_id": "synthetic-reviewer",
                    "role": "DATASET_CUSTODIAN",
                },
                "reviewed_at": "2026-09-10T00:01:00.000000Z",
                "approved_by": None,
                "approved_at": None,
                "team_gold_status": "DRAFT",
                "external_medical_review_status": "NOT_REQUESTED",
                "external_medical_approval_receipt_ref": None,
                "evidence_review_refs": [],
            },
        }
    )
    definition_hash = canonical_sha256(
        definition.model_dump(mode="json"),
        excluded_top_level_keys=frozenset({"suite_hash"}),
    )
    definition = definition.model_copy(update={"suite_hash": definition_hash})
    suite = SuiteResults.model_validate(
        {
            "schema_id": "rag-eval.suite-results",
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "suite_id": "required-suite",
            "suite_version": "1.0.0",
            "suite_definition_hash": definition_hash,
            "required": True,
            "expected_case_set_hash": case_set_hash,
            "executed_case_set_hash": case_set_hash,
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
            "artifact_hash": None,
        }
    )
    digest = canonical_sha256(suite.model_dump(mode="json"))
    return SuiteEvidence(
        suite=suite,
        definition=definition,
        artifact_ref=ImmutableReference(id="required-suite", version="1.0.0", hash=digest),
    )


def _suite_ref(evidence: SuiteEvidence) -> ImmutableReference:
    return ImmutableReference(
        id=evidence.definition.suite_id,
        version=evidence.definition.suite_version,
        hash=evidence.definition.suite_hash,
    )


def test_required_suite_definition_hash_mismatch_is_invalid() -> None:
    suite_evidence = _suite_evidence()
    policy = replace(_policy(), required_suites=(_suite_ref(suite_evidence),))
    mismatched = replace(
        suite_evidence,
        suite=suite_evidence.suite.model_copy(update={"suite_definition_hash": "7" * 64}),
    )
    evidence = replace(_evidence(), suites=(mismatched,))

    gate = build_release_gate(policy, evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "REQUIRED_SUITE_BINDING_MISMATCH:required-suite" in gate.blocking_reason_codes


def test_required_suite_internal_identity_cannot_be_spoofed_by_wrapper_ref() -> None:
    suite_evidence = _suite_evidence()
    policy = replace(_policy(), required_suites=(_suite_ref(suite_evidence),))
    spoofed = replace(
        suite_evidence,
        suite=suite_evidence.suite.model_copy(update={"suite_id": "unrelated-suite"}),
    )

    gate = build_release_gate(policy, replace(_evidence(), suites=(spoofed,)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "REQUIRED_SUITE_BINDING_MISMATCH:required-suite" in gate.blocking_reason_codes


def test_required_suite_must_be_required_and_cover_expected_cases() -> None:
    suite_evidence = _suite_evidence()
    policy = replace(_policy(), required_suites=(_suite_ref(suite_evidence),))
    invalid_suite = replace(
        suite_evidence,
        suite=suite_evidence.suite.model_copy(update={"required": False, "executed_case_set_hash": "0" * 64}),
    )

    gate = build_release_gate(policy, replace(_evidence(), suites=(invalid_suite,)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_SUITE_BINDING_MISMATCH:required-suite" in gate.blocking_reason_codes


def test_required_suite_case_hashes_are_bound_to_signed_definition() -> None:
    suite_evidence = _suite_evidence()
    policy = replace(_policy(), required_suites=(_suite_ref(suite_evidence),))
    tampered_suite = suite_evidence.suite.model_copy(
        update={
            "expected_case_set_hash": "0" * 64,
            "executed_case_set_hash": "0" * 64,
        }
    )
    tampered_hash = canonical_sha256(tampered_suite.model_dump(mode="json"))
    tampered = replace(
        suite_evidence,
        suite=tampered_suite,
        artifact_ref=ImmutableReference(
            id="required-suite",
            version="1.0.0",
            hash=tampered_hash,
        ),
    )

    gate = build_release_gate(policy, replace(_evidence(), suites=(tampered,)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_SUITE_BINDING_MISMATCH:required-suite" in gate.blocking_reason_codes


def test_required_suite_rejects_executed_case_set_different_from_approved_set() -> None:
    suite_evidence = _suite_evidence()
    policy = replace(_policy(), required_suites=(_suite_ref(suite_evidence),))
    unrelated_case = suite_evidence.suite.case_results[0].model_copy(update={"case_code": "unrelated-case"})
    executed_hash = canonical_sha256({"case_ids": ["unrelated-case"]})
    substituted_suite = suite_evidence.suite.model_copy(
        update={
            "case_results": (unrelated_case,),
            "executed_case_set_hash": executed_hash,
        }
    )
    substituted_artifact_hash = canonical_sha256(substituted_suite.model_dump(mode="json"))
    substituted_evidence = replace(
        suite_evidence,
        suite=substituted_suite,
        artifact_ref=ImmutableReference(
            id="required-suite",
            version="1.0.0",
            hash=substituted_artifact_hash,
        ),
    )

    gate = build_release_gate(
        policy,
        replace(_evidence(), suites=(substituted_evidence,)),
    )

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None
    assert "REQUIRED_SUITE_BINDING_MISMATCH:required-suite" in gate.blocking_reason_codes


def test_invalid_profile_evidence_is_not_overwritten_by_missing_execution() -> None:
    evidence = replace(
        _evidence(),
        required_scope_manifest_hash="0" * 64,
        completed_experiment_types=(),
    )

    gate = build_release_gate(_policy(), evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.blocking_execution_statuses == (
        ExecutionStatus.INVALID,
        ExecutionStatus.NOT_EVALUATED,
    )
    assert "REQUIRED_EXPERIMENT_NOT_COMPLETED" in gate.blocking_reason_codes


def test_runtime_policy_without_baseline_freeze_receipt_is_invalid() -> None:
    policy = replace(
        _policy(),
        required_receipts=tuple(item for item in _policy().required_receipts if item.id != "baseline-freeze-receipt"),
    )
    evidence = replace(
        _evidence(),
        receipts=tuple(item for item in _evidence().receipts if item.reference.id != "baseline-freeze-receipt"),
    )

    gate = build_release_gate(policy, evidence)

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "RELEASE_POLICY_PAIRED_REQUIREMENTS_INVALID" in gate.blocking_reason_codes


def test_exit_code_rejects_pass_with_blocking_reasons() -> None:
    gate = build_release_gate(_policy(), _evidence())
    contradictory = gate.model_copy(update={"blocking_reason_codes": ("MANUAL_TAMPER",)})

    assert release_gate_exit_code(contradictory) == 2


def test_completed_receipt_requires_exact_artifact_reference() -> None:
    evidence = _evidence()
    receipt = replace(evidence.receipts[0], artifact_ref=None)

    gate = build_release_gate(_policy(), replace(evidence, receipts=(receipt, evidence.receipts[1])))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert gate.aggregate_decision_status is None


def test_malformed_receipt_state_becomes_invalid_gate_result() -> None:
    for execution_status, decision_status in (
        (ExecutionStatus.COMPLETED, None),
        (ExecutionStatus.NOT_EVALUATED, DecisionStatus.PASS),
        (ExecutionStatus.COMPLETED, DecisionStatus.NOT_APPLICABLE),
    ):
        evidence = _evidence()
        receipt = replace(
            evidence.receipts[0],
            execution_status=execution_status,
            decision_status=decision_status,
        )

        gate = build_release_gate(
            _policy(),
            replace(evidence, receipts=(receipt, evidence.receipts[1])),
        )

        assert gate.aggregate_execution_status is ExecutionStatus.INVALID
        assert "REQUIRED_RECEIPT_STATE_INVALID:baseline-freeze-receipt" in gate.blocking_reason_codes


def test_expired_required_receipt_is_invalid() -> None:
    evidence = _evidence()
    receipt = replace(evidence.receipts[0], is_current=False)

    gate = build_release_gate(_policy(), replace(evidence, receipts=(receipt, evidence.receipts[1])))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_RECEIPT_EXPIRED:baseline-freeze-receipt" in gate.blocking_reason_codes


def test_duplicate_required_suite_identity_is_invalid() -> None:
    suite = _suite_evidence()
    policy = replace(_policy(), required_suites=(_suite_ref(suite),))

    gate = build_release_gate(policy, replace(_evidence(), suites=(suite, suite)))

    assert gate.aggregate_execution_status is ExecutionStatus.INVALID
    assert "REQUIRED_SUITE_DUPLICATE:required-suite" in gate.blocking_reason_codes
