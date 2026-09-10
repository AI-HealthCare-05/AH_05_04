from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class PairedCaseEvidence:
    receipt_id: str
    required_case_ids: tuple[str, ...]
    baseline_case_ids: tuple[str, ...]
    candidate_case_ids: tuple[str, ...]
    final_case_ids: tuple[str, ...]
    required_control_keys: tuple[str, ...]
    baseline_control_hashes: tuple[str, ...]
    candidate_control_hashes: tuple[str, ...]
    final_control_hashes: tuple[str, ...]
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


def _metric_members(policy: ReleaseGatePolicy, evidence: GateEvidence, reasons: set[str]) -> list[RequiredGateMember]:
    actual: dict[tuple[str, str, Partition, str], MetricEvidence] = {}
    duplicate_keys: set[tuple[str, str, Partition, str]] = set()
    for item in evidence.metrics:
        key = (
            item.metric.metric_id,
            item.metric.metric_version,
            item.metric.partition,
            item.metric.slice_id,
        )
        if key in actual:
            duplicate_keys.add(key)
        else:
            actual[key] = item
    members: list[RequiredGateMember] = []
    for requirement in policy.required_metrics:
        key = (
            requirement.metric_id,
            requirement.metric_version,
            requirement.partition,
            requirement.slice_id,
        )
        metric_evidence = actual.get(key)
        if key in duplicate_keys:
            reasons.add(f"REQUIRED_METRIC_DUPLICATE:{requirement.member_id}")
            members.append(
                RequiredGateMember(
                    member_type=GateMemberType.METRIC,
                    member_id=requirement.member_id,
                    member_version=requirement.metric_version,
                    member_hash=requirement.requirement_hash,
                    execution_status=ExecutionStatus.INVALID,
                    decision_status=None,
                    receipt_or_artifact_ref=None,
                )
            )
            continue
        if metric_evidence is None:
            reasons.add(f"REQUIRED_METRIC_MISSING:{requirement.member_id}")
            members.append(
                RequiredGateMember(
                    member_type=GateMemberType.METRIC,
                    member_id=requirement.member_id,
                    member_version=requirement.metric_version,
                    member_hash=requirement.requirement_hash,
                    execution_status=ExecutionStatus.NOT_EVALUATED,
                    decision_status=None,
                    receipt_or_artifact_ref=None,
                )
            )
            continue
        metric = metric_evidence.metric
        if metric.decision_status is DecisionStatus.FAIL:
            reasons.add(f"REQUIRED_METRIC_FAILED:{requirement.member_id}")
        if metric.decision_status is DecisionStatus.INCONCLUSIVE:
            reasons.add(f"REQUIRED_METRIC_INCONCLUSIVE:{requirement.member_id}")
        members.append(
            RequiredGateMember(
                member_type=GateMemberType.METRIC,
                member_id=requirement.member_id,
                member_version=requirement.metric_version,
                member_hash=metric_evidence.artifact_ref.hash,
                execution_status=metric.execution_status,
                decision_status=metric.decision_status,
                receipt_or_artifact_ref=metric_evidence.artifact_ref,
            )
        )
    return members


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
        elif suite_evidence.artifact_ref.hash != expected.hash:
            status = ExecutionStatus.INVALID
            decision = None
            artifact_ref = suite_evidence.artifact_ref
            member_hash = suite_evidence.artifact_ref.hash
            reasons.add(f"REQUIRED_SUITE_HASH_MISMATCH:{expected.id}")
        else:
            status = suite_evidence.suite.aggregate_execution_status
            decision = suite_evidence.suite.aggregate_decision_status
            artifact_ref = suite_evidence.artifact_ref
            member_hash = suite_evidence.artifact_ref.hash
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


def _receipt_members(policy: ReleaseGatePolicy, evidence: GateEvidence, reasons: set[str]) -> list[RequiredGateMember]:
    actual: dict[tuple[str, str], ReceiptEvidence] = {}
    duplicate_keys: set[tuple[str, str]] = set()
    for item in evidence.receipts:
        key = (item.reference.id, item.reference.version)
        if key in actual:
            duplicate_keys.add(key)
        else:
            actual[key] = item
    members: list[RequiredGateMember] = []
    for expected in policy.required_receipts:
        key = (expected.id, expected.version)
        receipt_evidence = actual.get(key)
        if key in duplicate_keys:
            status = ExecutionStatus.INVALID
            decision = None
            artifact_ref = None
            member_hash = expected.hash
            reasons.add(f"REQUIRED_RECEIPT_DUPLICATE:{expected.id}")
        elif receipt_evidence is None:
            status = ExecutionStatus.NOT_EVALUATED
            decision = None
            artifact_ref = None
            member_hash = expected.hash
            reasons.add(_reason("MISSING", expected.id))
        elif receipt_evidence.reference.hash != expected.hash:
            status = ExecutionStatus.INVALID
            decision = None
            artifact_ref = receipt_evidence.artifact_ref
            member_hash = receipt_evidence.reference.hash
            reasons.add(_reason("HASH_MISMATCH", expected.id))
        else:
            status = receipt_evidence.execution_status
            decision = receipt_evidence.decision_status
            artifact_ref = receipt_evidence.artifact_ref
            member_hash = receipt_evidence.reference.hash
            if decision is DecisionStatus.FAIL:
                reasons.add(f"REQUIRED_RECEIPT_FAILED:{expected.id}")
            elif decision is DecisionStatus.INCONCLUSIVE:
                reasons.add(f"REQUIRED_RECEIPT_INCONCLUSIVE:{expected.id}")
        members.append(
            RequiredGateMember(
                member_type=GateMemberType.CONTRACT_RECEIPT,
                member_id=expected.id,
                member_version=expected.version,
                member_hash=member_hash,
                execution_status=status,
                decision_status=decision,
                receipt_or_artifact_ref=artifact_ref,
            )
        )
    return members


def _apply_paired_case_checks(
    members: list[RequiredGateMember],
    paired: PairedCaseEvidence | None,
    reasons: set[str],
) -> list[RequiredGateMember]:
    if paired is None:
        return members
    index = next((position for position, item in enumerate(members) if item.member_id == paired.receipt_id), None)
    if index is None:
        reasons.add("PAIRED_COMPARISON_RECEIPT_NOT_REQUIRED")
        return members
    member = members[index]
    if member.execution_status is not ExecutionStatus.COMPLETED:
        return members
    case_collections = (
        paired.required_case_ids,
        paired.baseline_case_ids,
        paired.candidate_case_ids,
        paired.final_case_ids,
    )
    case_sets_match = all(len(values) == len(set(values)) for values in case_collections) and all(
        set(values) == set(paired.required_case_ids) for values in case_collections[1:]
    )
    control_shapes_match = all(
        len(values) == len(paired.required_control_keys)
        for values in (
            paired.baseline_control_hashes,
            paired.candidate_control_hashes,
            paired.final_control_hashes,
        )
    )
    controls_match = (
        len(paired.required_control_keys) == len(set(paired.required_control_keys))
        and control_shapes_match
        and all(
            baseline == candidate == final
            for baseline, candidate, final in zip(
                paired.baseline_control_hashes,
                paired.candidate_control_hashes,
                paired.final_control_hashes,
                strict=True,
            )
        )
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
    receipts = _apply_paired_case_checks(receipts, evidence.paired_case_evidence, reasons)
    receipts.append(_profile_member(policy, evidence, reasons))
    receipt_members = tuple(sorted(receipts, key=lambda item: item.member_id))
    aggregate_status, aggregate_decision, blockers = _aggregate((*metrics, *suites, *receipt_members))
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
