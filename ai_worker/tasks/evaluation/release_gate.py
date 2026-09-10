from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ai_worker.tasks.evaluation.schemas.artifacts import (
    GateMemberType,
    GateResult,
    MetricResult,
    RequiredGateMember,
    SuiteResults,
)
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
    Partition,
)

_BLOCKING_STATUS_ORDER = {
    ExecutionStatus.INVALID: 0,
    ExecutionStatus.ERROR: 1,
    ExecutionStatus.NOT_IMPLEMENTED: 2,
    ExecutionStatus.NOT_EVALUATED: 3,
}


@dataclass(frozen=True, slots=True)
class MetricRequirement:
    metric_id: str
    metric_version: str
    partition: Partition
    slice_id: str
    requirement_hash: str
    unit_of_analysis: str
    estimator_id: str
    estimator_version: str
    minimum_case_count: int
    independence_unit: str | None
    cluster_dimension: str | None
    minimum_independent_group_count: int | None
    threshold: str
    decision_basis: str
    ci_method_id: str
    ci_method_version: str
    ci_level: str | None

    @property
    def member_id(self) -> str:
        return f"{self.metric_id}:{self.partition.value}:{self.slice_id}"


@dataclass(frozen=True, slots=True)
class ReleaseGatePolicy:
    evaluation_policy_ref: ImmutableReference
    evaluation_profile_ref: ImmutableReference
    comparison_policy_ref: ImmutableReference
    runtime_eligible: bool
    required_experiment_types: tuple[ExperimentType, ...]
    required_partitions: tuple[Partition, ...]
    required_metrics: tuple[MetricRequirement, ...]
    required_suites: tuple[ImmutableReference, ...]
    required_receipts: tuple[ImmutableReference, ...]
    paired_comparison_receipt_id: str | None
    required_case_ids: tuple[str, ...]
    controlled_variable_keys: tuple[str, ...]
    required_scope_manifest_hash: str


@dataclass(frozen=True, slots=True)
class MetricEvidence:
    metric: MetricResult
    artifact_ref: ImmutableReference


@dataclass(frozen=True, slots=True)
class SuiteEvidence:
    suite: SuiteResults
    artifact_ref: ImmutableReference


@dataclass(frozen=True, slots=True)
class ReceiptEvidence:
    reference: ImmutableReference
    execution_status: ExecutionStatus
    decision_status: DecisionStatus | None
    artifact_ref: ImmutableReference | None
    is_current: bool


@dataclass(frozen=True, slots=True)
class ControlSettingEvidence:
    variable_key: str
    baseline_hash: str
    candidate_hash: str
    final_hash: str


@dataclass(frozen=True, slots=True)
class PairedCaseEvidence:
    receipt_id: str
    receipt_hash: str
    baseline_case_ids: tuple[str, ...]
    candidate_case_ids: tuple[str, ...]
    final_case_ids: tuple[str, ...]
    control_settings: tuple[ControlSettingEvidence, ...]
    paired_delta_complete: bool


@dataclass(frozen=True, slots=True)
class GateEvidence:
    run_id: str
    required_scope_manifest_hash: str
    completed_experiment_types: tuple[ExperimentType, ...]
    completed_partitions: tuple[Partition, ...]
    metrics: tuple[MetricEvidence, ...]
    suites: tuple[SuiteEvidence, ...]
    receipts: tuple[ReceiptEvidence, ...]
    paired_case_evidence: PairedCaseEvidence | None


def _reason(prefix: str, identifier: str) -> str:
    if identifier == "baseline-freeze-receipt":
        return f"BASELINE_FREEZE_RECEIPT_{prefix}"
    return f"REQUIRED_RECEIPT_{prefix}:{identifier}"


def _profile_member(policy: ReleaseGatePolicy, evidence: GateEvidence, reasons: set[str]) -> RequiredGateMember:
    status = ExecutionStatus.COMPLETED
    decision: DecisionStatus | None = DecisionStatus.PASS
    if not policy.runtime_eligible:
        status = ExecutionStatus.NOT_EVALUATED
        decision = None
        reasons.add("PROFILE_NOT_RUNTIME_ELIGIBLE")
    elif (
        policy.paired_comparison_receipt_id is None
        or policy.paired_comparison_receipt_id not in {item.id for item in policy.required_receipts}
        or not policy.required_case_ids
        or len(policy.required_case_ids) != len(set(policy.required_case_ids))
        or not policy.controlled_variable_keys
        or len(policy.controlled_variable_keys) != len(set(policy.controlled_variable_keys))
    ):
        status = ExecutionStatus.INVALID
        decision = None
        reasons.add("RELEASE_POLICY_PAIRED_REQUIREMENTS_INVALID")
    if evidence.required_scope_manifest_hash != policy.required_scope_manifest_hash:
        status = ExecutionStatus.INVALID
        decision = None
        reasons.add("REQUIRED_SCOPE_MANIFEST_HASH_MISMATCH")
    missing_experiments = set(policy.required_experiment_types) - set(evidence.completed_experiment_types)
    missing_partitions = set(policy.required_partitions) - set(evidence.completed_partitions)
    if missing_experiments:
        status = ExecutionStatus.NOT_EVALUATED
        decision = None
        reasons.add("REQUIRED_EXPERIMENT_NOT_COMPLETED")
    if missing_partitions:
        status = ExecutionStatus.NOT_EVALUATED
        decision = None
        reasons.add("REQUIRED_PARTITION_NOT_COMPLETED")
    return RequiredGateMember(
        member_type=GateMemberType.CONTRACT_RECEIPT,
        member_id="release-profile-readiness",
        member_version=policy.evaluation_profile_ref.version,
        member_hash=policy.evaluation_profile_ref.hash,
        execution_status=status,
        decision_status=decision,
        receipt_or_artifact_ref=policy.evaluation_profile_ref,
    )


def _metric_member(
    requirement: MetricRequirement,
    metric_evidence: MetricEvidence | None,
    *,
    duplicate: bool,
    reasons: set[str],
) -> RequiredGateMember:
    if duplicate or metric_evidence is None:
        label = "DUPLICATE" if duplicate else "MISSING"
        status = ExecutionStatus.INVALID if duplicate else ExecutionStatus.NOT_EVALUATED
        reasons.add(f"REQUIRED_METRIC_{label}:{requirement.member_id}")
        return RequiredGateMember(
            member_type=GateMemberType.METRIC,
            member_id=requirement.member_id,
            member_version=requirement.metric_version,
            member_hash=requirement.requirement_hash,
            execution_status=status,
            decision_status=None,
            receipt_or_artifact_ref=None,
        )
    metric = metric_evidence.metric
    if not _metric_metadata_matches(requirement, metric):
        reasons.add(f"REQUIRED_METRIC_POLICY_MISMATCH:{requirement.member_id}")
        return _metric_result_member(requirement, metric_evidence, ExecutionStatus.INVALID, None)
    status, decision = _metric_outcome(requirement, metric, reasons)
    return _metric_result_member(requirement, metric_evidence, status, decision)


def _metric_metadata_matches(requirement: MetricRequirement, metric: MetricResult) -> bool:
    return (
        metric.required
        and metric.unit_of_analysis == requirement.unit_of_analysis
        and metric.estimator_id == requirement.estimator_id
        and metric.estimator_version == requirement.estimator_version
        and metric.independence_unit == requirement.independence_unit
        and metric.cluster_dimension == requirement.cluster_dimension
        and metric.threshold == requirement.threshold
        and metric.ci_method_id == requirement.ci_method_id
        and metric.ci_method_version == requirement.ci_method_version
        and metric.ci_level == requirement.ci_level
    )


def _metric_outcome(
    requirement: MetricRequirement,
    metric: MetricResult,
    reasons: set[str],
) -> tuple[ExecutionStatus, DecisionStatus | None]:
    if metric.execution_status is not ExecutionStatus.COMPLETED:
        reasons.add(f"REQUIRED_METRIC_INCOMPLETE:{requirement.member_id}")
        return metric.execution_status, None
    if _metric_is_insufficient(requirement, metric):
        reasons.add(f"REQUIRED_METRIC_INCONCLUSIVE:{requirement.member_id}")
        return ExecutionStatus.COMPLETED, DecisionStatus.INCONCLUSIVE
    expected = _metric_threshold_decision(requirement, metric)
    if expected is None:
        reasons.add(f"REQUIRED_METRIC_DECISION_BASIS_UNSUPPORTED:{requirement.member_id}")
        return ExecutionStatus.COMPLETED, DecisionStatus.INCONCLUSIVE
    if metric.decision_status is not expected:
        reasons.add(f"REQUIRED_METRIC_DECISION_MISMATCH:{requirement.member_id}")
        return ExecutionStatus.INVALID, None
    if expected is DecisionStatus.FAIL:
        reasons.add(f"REQUIRED_METRIC_FAILED:{requirement.member_id}")
    return ExecutionStatus.COMPLETED, expected


def _metric_is_insufficient(requirement: MetricRequirement, metric: MetricResult) -> bool:
    return (
        metric.sample_case_count is None
        or metric.sample_case_count < requirement.minimum_case_count
        or metric.sample_independent_group_count is None
        or (
            requirement.minimum_independent_group_count is not None
            and metric.sample_independent_group_count < requirement.minimum_independent_group_count
        )
        or metric.denominator is None
        or metric.denominator == 0
        or metric.ci_lower is None
        or metric.ci_upper is None
    )


def _metric_result_member(
    requirement: MetricRequirement,
    evidence: MetricEvidence,
    status: ExecutionStatus,
    decision: DecisionStatus | None,
) -> RequiredGateMember:
    return RequiredGateMember(
        member_type=GateMemberType.METRIC,
        member_id=requirement.member_id,
        member_version=requirement.metric_version,
        member_hash=evidence.artifact_ref.hash,
        execution_status=status,
        decision_status=decision,
        receipt_or_artifact_ref=evidence.artifact_ref,
    )


def _metric_members(policy: ReleaseGatePolicy, evidence: GateEvidence, reasons: set[str]) -> list[RequiredGateMember]:
    actual: dict[tuple[str, str, Partition, str], MetricEvidence] = {}
    duplicate_keys: set[tuple[str, str, Partition, str]] = set()
    for item in evidence.metrics:
        key = (item.metric.metric_id, item.metric.metric_version, item.metric.partition, item.metric.slice_id)
        if key in actual:
            duplicate_keys.add(key)
        else:
            actual[key] = item
    return [
        _metric_member(
            requirement,
            actual.get(
                (requirement.metric_id, requirement.metric_version, requirement.partition, requirement.slice_id)
            ),
            duplicate=(
                requirement.metric_id,
                requirement.metric_version,
                requirement.partition,
                requirement.slice_id,
            )
            in duplicate_keys,
            reasons=reasons,
        )
        for requirement in policy.required_metrics
    ]


def _metric_threshold_decision(
    requirement: MetricRequirement,
    metric: MetricResult,
) -> DecisionStatus | None:
    if requirement.decision_basis == "ZERO_FAILURES":
        return DecisionStatus.PASS if metric.numerator == 0 else DecisionStatus.FAIL
    if metric.metric_value is None:
        return None
    value = Decimal(metric.metric_value)
    threshold = Decimal(requirement.threshold)
    if requirement.decision_basis == "AT_LEAST":
        return DecisionStatus.PASS if value >= threshold else DecisionStatus.FAIL
    if requirement.decision_basis == "AT_MOST":
        return DecisionStatus.PASS if value <= threshold else DecisionStatus.FAIL
    return None


def _suite_members(policy: ReleaseGatePolicy, evidence: GateEvidence, reasons: set[str]) -> list[RequiredGateMember]:
    actual: dict[tuple[str, str], SuiteEvidence] = {}
    duplicate_keys: set[tuple[str, str]] = set()
    for item in evidence.suites:
        key = (item.artifact_ref.id, item.artifact_ref.version)
        if key in actual:
            duplicate_keys.add(key)
        else:
            actual[key] = item
    members: list[RequiredGateMember] = []
    for expected in policy.required_suites:
        key = (expected.id, expected.version)
        suite_evidence = actual.get(key)
        if key in duplicate_keys:
            status = ExecutionStatus.INVALID
            decision = None
            artifact_ref = None
            member_hash = expected.hash
            reasons.add(f"REQUIRED_SUITE_DUPLICATE:{expected.id}")
        elif suite_evidence is None:
            status = ExecutionStatus.NOT_EVALUATED
            decision = None
            artifact_ref = None
            member_hash = expected.hash
            reasons.add(f"REQUIRED_SUITE_MISSING:{expected.id}")
        elif (
            suite_evidence.suite.run_id != evidence.run_id
            or suite_evidence.suite.suite_id != expected.id
            or suite_evidence.suite.suite_version != expected.version
            or suite_evidence.suite.suite_definition_hash != expected.hash
            or suite_evidence.suite.artifact_hash is None
            or suite_evidence.suite.artifact_hash != suite_evidence.artifact_ref.hash
        ):
            status = ExecutionStatus.INVALID
            decision = None
            artifact_ref = suite_evidence.artifact_ref
            member_hash = suite_evidence.artifact_ref.hash
            reasons.add(f"REQUIRED_SUITE_BINDING_MISMATCH:{expected.id}")
        else:
            status = suite_evidence.suite.aggregate_execution_status
            decision = suite_evidence.suite.aggregate_decision_status
            artifact_ref = suite_evidence.artifact_ref
            member_hash = suite_evidence.artifact_ref.hash
            if decision is DecisionStatus.FAIL:
                reasons.add(f"REQUIRED_SUITE_FAILED:{expected.id}")
            elif decision is DecisionStatus.INCONCLUSIVE:
                reasons.add(f"REQUIRED_SUITE_INCONCLUSIVE:{expected.id}")
            elif status is not ExecutionStatus.COMPLETED:
                reasons.add(f"REQUIRED_SUITE_INCOMPLETE:{expected.id}")
        members.append(
            RequiredGateMember(
                member_type=GateMemberType.SUITE,
                member_id=expected.id,
                member_version=expected.version,
                member_hash=member_hash,
                execution_status=status,
                decision_status=decision,
                receipt_or_artifact_ref=artifact_ref,
            )
        )
    return members


def _receipt_member(
    expected: ImmutableReference,
    evidence: ReceiptEvidence | None,
    *,
    duplicate: bool,
    reasons: set[str],
) -> RequiredGateMember:
    if duplicate or evidence is None:
        label = "DUPLICATE" if duplicate else "MISSING"
        status = ExecutionStatus.INVALID if duplicate else ExecutionStatus.NOT_EVALUATED
        reason = f"REQUIRED_RECEIPT_DUPLICATE:{expected.id}" if duplicate else _reason(label, expected.id)
        reasons.add(reason)
        return _receipt_result_member(expected, expected.hash, status, None, None)
    if evidence.reference.hash != expected.hash:
        reasons.add(_reason("HASH_MISMATCH", expected.id))
        return _receipt_result_member(
            expected, evidence.reference.hash, ExecutionStatus.INVALID, None, evidence.artifact_ref
        )
    if not evidence.is_current:
        reasons.add(f"REQUIRED_RECEIPT_EXPIRED:{expected.id}")
        return _receipt_result_member(
            expected, evidence.reference.hash, ExecutionStatus.INVALID, None, evidence.artifact_ref
        )
    if evidence.execution_status is ExecutionStatus.COMPLETED and evidence.artifact_ref != evidence.reference:
        reasons.add(f"REQUIRED_RECEIPT_ARTIFACT_REF_MISMATCH:{expected.id}")
        return _receipt_result_member(
            expected, evidence.reference.hash, ExecutionStatus.INVALID, None, evidence.artifact_ref
        )
    if evidence.decision_status is DecisionStatus.FAIL:
        reasons.add(f"REQUIRED_RECEIPT_FAILED:{expected.id}")
    elif evidence.decision_status is DecisionStatus.INCONCLUSIVE:
        reasons.add(f"REQUIRED_RECEIPT_INCONCLUSIVE:{expected.id}")
    elif evidence.execution_status is not ExecutionStatus.COMPLETED:
        reasons.add(f"REQUIRED_RECEIPT_INCOMPLETE:{expected.id}")
    return _receipt_result_member(
        expected,
        evidence.reference.hash,
        evidence.execution_status,
        evidence.decision_status,
        evidence.artifact_ref,
    )


def _receipt_result_member(
    expected: ImmutableReference,
    member_hash: str,
    status: ExecutionStatus,
    decision: DecisionStatus | None,
    artifact_ref: ImmutableReference | None,
) -> RequiredGateMember:
    return RequiredGateMember(
        member_type=GateMemberType.CONTRACT_RECEIPT,
        member_id=expected.id,
        member_version=expected.version,
        member_hash=member_hash,
        execution_status=status,
        decision_status=decision,
        receipt_or_artifact_ref=artifact_ref,
    )


def _receipt_members(policy: ReleaseGatePolicy, evidence: GateEvidence, reasons: set[str]) -> list[RequiredGateMember]:
    actual: dict[tuple[str, str], ReceiptEvidence] = {}
    duplicate_keys: set[tuple[str, str]] = set()
    for item in evidence.receipts:
        key = (item.reference.id, item.reference.version)
        if key in actual:
            duplicate_keys.add(key)
        else:
            actual[key] = item
    return [
        _receipt_member(
            expected,
            actual.get((expected.id, expected.version)),
            duplicate=(expected.id, expected.version) in duplicate_keys,
            reasons=reasons,
        )
        for expected in policy.required_receipts
    ]


def _apply_paired_case_checks(
    policy: ReleaseGatePolicy,
    members: list[RequiredGateMember],
    paired: PairedCaseEvidence | None,
    reasons: set[str],
) -> list[RequiredGateMember]:
    required_receipt_id = policy.paired_comparison_receipt_id
    if required_receipt_id is None:
        return members
    index = next((position for position, item in enumerate(members) if item.member_id == required_receipt_id), None)
    if index is None:
        return members
    member = members[index]
    if member.execution_status is not ExecutionStatus.COMPLETED:
        return members
    if paired is None:
        reasons.add("PAIRED_COMPARISON_EVIDENCE_MISSING")
        members[index] = member.model_copy(
            update={"execution_status": ExecutionStatus.NOT_EVALUATED, "decision_status": None}
        )
        return members
    if paired.receipt_id != required_receipt_id:
        reasons.add("PAIRED_COMPARISON_RECEIPT_MISMATCH")
        members[index] = member.model_copy(
            update={"execution_status": ExecutionStatus.INVALID, "decision_status": None}
        )
        return members
    if paired.receipt_hash != member.member_hash:
        reasons.add("PAIRED_COMPARISON_HASH_MISMATCH")
        members[index] = member.model_copy(
            update={"execution_status": ExecutionStatus.INVALID, "decision_status": None}
        )
        return members
    case_collections = (
        paired.baseline_case_ids,
        paired.candidate_case_ids,
        paired.final_case_ids,
    )
    case_sets_match = all(len(values) == len(set(values)) for values in case_collections) and all(
        set(values) == set(policy.required_case_ids) for values in case_collections
    )
    control_keys = tuple(item.variable_key for item in paired.control_settings)
    controls_match = (
        len(control_keys) == len(set(control_keys))
        and set(control_keys) == set(policy.controlled_variable_keys)
        and all(item.baseline_hash == item.candidate_hash == item.final_hash for item in paired.control_settings)
    )
    if not case_sets_match:
        reasons.add("PAIRED_CASE_SET_MISMATCH")
        members[index] = member.model_copy(
            update={"execution_status": ExecutionStatus.INVALID, "decision_status": None}
        )
    elif not controls_match:
        reasons.add("PAIRED_CONTROL_SETTING_MISMATCH")
        members[index] = member.model_copy(
            update={"execution_status": ExecutionStatus.INVALID, "decision_status": None}
        )
    elif not paired.paired_delta_complete:
        reasons.add("PAIRED_DELTA_OR_CI_MISSING")
        members[index] = member.model_copy(
            update={
                "execution_status": ExecutionStatus.COMPLETED,
                "decision_status": DecisionStatus.INCONCLUSIVE,
            }
        )
    return members


def _aggregate(
    members: tuple[RequiredGateMember, ...],
) -> tuple[ExecutionStatus, DecisionStatus | None, tuple[ExecutionStatus, ...]]:
    blocking = tuple(
        sorted(
            {item.execution_status for item in members if item.execution_status is not ExecutionStatus.COMPLETED},
            key=lambda status: _BLOCKING_STATUS_ORDER[status],
        )
    )
    if blocking:
        return blocking[0], None, blocking
    decisions = {item.decision_status for item in members}
    if DecisionStatus.FAIL in decisions:
        return ExecutionStatus.COMPLETED, DecisionStatus.FAIL, ()
    if DecisionStatus.INCONCLUSIVE in decisions:
        return ExecutionStatus.COMPLETED, DecisionStatus.INCONCLUSIVE, ()
    return ExecutionStatus.COMPLETED, DecisionStatus.PASS, ()


def build_release_gate(policy: ReleaseGatePolicy, evidence: GateEvidence) -> GateResult:
    """Build the deterministic gate artifact from already validated, non-sensitive evidence."""

    reasons: set[str] = set()
    metrics = tuple(sorted(_metric_members(policy, evidence, reasons), key=lambda item: item.member_id))
    suites = tuple(sorted(_suite_members(policy, evidence, reasons), key=lambda item: item.member_id))
    receipts = _receipt_members(policy, evidence, reasons)
    receipts = _apply_paired_case_checks(policy, receipts, evidence.paired_case_evidence, reasons)
    receipts.append(_profile_member(policy, evidence, reasons))
    receipt_members = tuple(sorted(receipts, key=lambda item: item.member_id))
    aggregate_status, aggregate_decision, blockers = _aggregate((*metrics, *suites, *receipt_members))
    if aggregate_decision is DecisionStatus.PASS and reasons:
        raise ValueError("release gate PASS must not contain blocking reasons")
    return GateResult(
        schema_id="rag-eval.gate",
        schema_version="1.0.0",
        run_id=evidence.run_id,
        evaluation_policy_ref=policy.evaluation_policy_ref,
        evaluation_profile_ref=policy.evaluation_profile_ref,
        comparison_policy_ref=policy.comparison_policy_ref,
        required_scope_manifest_hash=evidence.required_scope_manifest_hash,
        required_metrics=metrics,
        required_suites=suites,
        required_contract_receipts=receipt_members,
        aggregate_execution_status=aggregate_status,
        aggregate_decision_status=aggregate_decision,
        blocking_execution_statuses=blockers,
        blocking_reason_codes=tuple(sorted(reasons)),
    )


def release_gate_exit_code(gate: GateResult) -> int:
    """Map a validated gate result to the public CLI outcome contract."""

    if (
        gate.aggregate_execution_status is ExecutionStatus.COMPLETED
        and gate.aggregate_decision_status is DecisionStatus.PASS
    ):
        return 0
    if (
        gate.aggregate_execution_status is ExecutionStatus.COMPLETED
        and gate.aggregate_decision_status is DecisionStatus.FAIL
    ):
        return 1
    return 2
