from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.schemas.artifacts import (
    GateMemberType,
    GateResult,
    MetricResult,
    MetricResults,
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
from ai_worker.tasks.evaluation.schemas.policy import SuiteDefinition

_BLOCKING_STATUS_ORDER = {
    ExecutionStatus.INVALID: 0,
    ExecutionStatus.ERROR: 1,
    ExecutionStatus.NOT_IMPLEMENTED: 2,
    ExecutionStatus.NOT_EVALUATED: 3,
}
_SIX_PLACES = Decimal("0.000001")


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
    ci_sidedness: str | None

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
    artifact: MetricResults
    artifact_ref: ImmutableReference


@dataclass(frozen=True, slots=True)
class SuiteEvidence:
    suite: SuiteResults
    definition: SuiteDefinition
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
    paired_delta_refs: tuple[ImmutableReference, ...]


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


def paired_case_manifest_hash(evidence: PairedCaseEvidence) -> str:
    """Hash the complete synthetic/immutable pairing payload, excluding its claimed hash."""

    payload = {
        "receipt_id": evidence.receipt_id,
        "baseline_case_ids": sorted(evidence.baseline_case_ids),
        "candidate_case_ids": sorted(evidence.candidate_case_ids),
        "final_case_ids": sorted(evidence.final_case_ids),
        "control_settings": [
            {
                "variable_key": item.variable_key,
                "baseline_hash": item.baseline_hash,
                "candidate_hash": item.candidate_hash,
                "final_hash": item.final_hash,
            }
            for item in sorted(evidence.control_settings, key=lambda item: item.variable_key)
        ],
        "paired_delta_refs": [
            item.model_dump(mode="json")
            for item in sorted(evidence.paired_delta_refs, key=lambda item: (item.id, item.version))
        ],
    }
    return canonical_sha256(cast(JsonValue, payload))


def _reason(prefix: str, identifier: str) -> str:
    if identifier == "baseline-freeze-receipt":
        return f"BASELINE_FREEZE_RECEIPT_{prefix}"
    return f"REQUIRED_RECEIPT_{prefix}:{identifier}"


def _readiness_member(
    member_id: str,
    member_version: str,
    member_hash: str,
    artifact_ref: ImmutableReference | None,
    status: ExecutionStatus,
) -> RequiredGateMember:
    return RequiredGateMember(
        member_type=GateMemberType.CONTRACT_RECEIPT,
        member_id=member_id,
        member_version=member_version,
        member_hash=member_hash,
        execution_status=status,
        decision_status=DecisionStatus.PASS if status is ExecutionStatus.COMPLETED else None,
        receipt_or_artifact_ref=artifact_ref,
    )


def _profile_members(
    policy: ReleaseGatePolicy,
    evidence: GateEvidence,
    reasons: set[str],
) -> list[RequiredGateMember]:
    profile_status = ExecutionStatus.COMPLETED
    if not policy.runtime_eligible:
        profile_status = ExecutionStatus.NOT_EVALUATED
        reasons.add("PROFILE_NOT_RUNTIME_ELIGIBLE")
    elif (
        policy.paired_comparison_receipt_id is None
        or policy.paired_comparison_receipt_id not in {item.id for item in policy.required_receipts}
        or "baseline-freeze-receipt" not in {item.id for item in policy.required_receipts}
        or not policy.required_case_ids
        or len(policy.required_case_ids) != len(set(policy.required_case_ids))
        or not policy.controlled_variable_keys
        or len(policy.controlled_variable_keys) != len(set(policy.controlled_variable_keys))
    ):
        profile_status = ExecutionStatus.INVALID
        reasons.add("RELEASE_POLICY_PAIRED_REQUIREMENTS_INVALID")

    scope_status = ExecutionStatus.COMPLETED
    if evidence.required_scope_manifest_hash != policy.required_scope_manifest_hash:
        scope_status = ExecutionStatus.INVALID
        reasons.add("REQUIRED_SCOPE_MANIFEST_HASH_MISMATCH")

    experiment_status = ExecutionStatus.COMPLETED
    if set(policy.required_experiment_types) - set(evidence.completed_experiment_types):
        experiment_status = ExecutionStatus.NOT_EVALUATED
        reasons.add("REQUIRED_EXPERIMENT_NOT_COMPLETED")

    partition_status = ExecutionStatus.COMPLETED
    if set(policy.required_partitions) - set(evidence.completed_partitions):
        partition_status = ExecutionStatus.NOT_EVALUATED
        reasons.add("REQUIRED_PARTITION_NOT_COMPLETED")

    return [
        _readiness_member(
            "release-profile-readiness",
            policy.evaluation_profile_ref.version,
            policy.evaluation_profile_ref.hash,
            policy.evaluation_profile_ref,
            profile_status,
        ),
        _readiness_member(
            "required-scope-manifest-readiness",
            policy.evaluation_policy_ref.version,
            policy.required_scope_manifest_hash,
            None,
            scope_status,
        ),
        _readiness_member(
            "required-experiment-readiness",
            policy.evaluation_profile_ref.version,
            policy.evaluation_profile_ref.hash,
            policy.evaluation_profile_ref,
            experiment_status,
        ),
        _readiness_member(
            "required-partition-readiness",
            policy.evaluation_profile_ref.version,
            policy.evaluation_profile_ref.hash,
            policy.evaluation_profile_ref,
            partition_status,
        ),
    ]


def _metric_member(
    requirement: MetricRequirement,
    metric_evidence: MetricEvidence | None,
    *,
    duplicate: bool,
    evidence_run_id: str,
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
    artifact_payload = cast(JsonValue, metric_evidence.artifact.model_dump(mode="json"))
    if (
        metric_evidence.artifact.run_id != evidence_run_id
        or metric not in metric_evidence.artifact.metrics
        or canonical_sha256(artifact_payload) != metric_evidence.artifact_ref.hash
    ):
        reasons.add(f"REQUIRED_METRIC_ARTIFACT_MISMATCH:{requirement.member_id}")
        return _metric_result_member(requirement, metric_evidence, ExecutionStatus.INVALID, None)
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
        and metric.ci_sidedness == requirement.ci_sidedness
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
    if not _metric_ci_is_valid(metric):
        reasons.add(f"REQUIRED_METRIC_CI_INVALID:{requirement.member_id}")
        return ExecutionStatus.INVALID, None
    value_matches = _metric_value_matches_counts(metric)
    if value_matches is None:
        reasons.add(f"REQUIRED_METRIC_VALUE_VALIDATION_UNSUPPORTED:{requirement.member_id}")
        return ExecutionStatus.NOT_IMPLEMENTED, None
    if not value_matches:
        reasons.add(f"REQUIRED_METRIC_VALUE_MISMATCH:{requirement.member_id}")
        return ExecutionStatus.INVALID, None
    expected = _metric_threshold_decision(requirement, metric)
    if expected is None:
        reasons.add(f"REQUIRED_METRIC_DECISION_BASIS_UNSUPPORTED:{requirement.member_id}")
        return ExecutionStatus.NOT_IMPLEMENTED, None
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


def _metric_ci_is_valid(metric: MetricResult) -> bool:
    if metric.metric_value is None or metric.ci_lower is None or metric.ci_upper is None:
        return False
    value = Decimal(metric.metric_value)
    lower = Decimal(metric.ci_lower)
    upper = Decimal(metric.ci_upper)
    return lower <= value <= upper


def _metric_value_matches_counts(metric: MetricResult) -> bool | None:
    if metric.metric_value is None or metric.numerator is None or metric.denominator is None:
        return False
    if metric.estimator_id == "COUNT":
        expected = Decimal(metric.numerator)
    elif metric.estimator_id in {"PROPORTION", "RATE"}:
        expected = (Decimal(metric.numerator) / Decimal(metric.denominator)).quantize(
            _SIX_PLACES,
            rounding=ROUND_HALF_EVEN,
        )
    elif metric.estimator_id == "CASE_MEAN":
        value = Decimal(metric.metric_value)
        return Decimal(0) <= value <= Decimal(1) and metric.numerator <= metric.denominator
    else:
        return None
    return Decimal(metric.metric_value) == expected


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
            evidence_run_id=evidence.run_id,
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
    if metric.metric_value is None or metric.ci_lower is None or metric.ci_upper is None:
        return None
    threshold = Decimal(requirement.threshold)
    if requirement.decision_basis == "AT_LEAST":
        return DecisionStatus.PASS if Decimal(metric.ci_lower) >= threshold else DecisionStatus.FAIL
    if requirement.decision_basis == "AT_MOST":
        return DecisionStatus.PASS if Decimal(metric.ci_upper) <= threshold else DecisionStatus.FAIL
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
        elif not _suite_binding_matches(expected, evidence.run_id, suite_evidence):
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


def _suite_binding_matches(
    expected: ImmutableReference,
    run_id: str,
    suite_evidence: SuiteEvidence,
) -> bool:
    suite = suite_evidence.suite
    definition = suite_evidence.definition
    payload = cast(JsonValue, suite.model_dump(mode="json"))
    definition_payload = cast(JsonValue, definition.model_dump(mode="json"))
    case_codes = [item.case_code for item in suite.case_results]
    executed_case_set_hash = canonical_sha256(cast(JsonValue, {"case_ids": sorted(case_codes)}))
    return not (
        suite.run_id != run_id
        or suite.suite_id != expected.id
        or suite.suite_version != expected.version
        or suite.suite_definition_hash != expected.hash
        or definition.suite_id != expected.id
        or definition.suite_version != expected.version
        or definition.suite_hash != expected.hash
        or canonical_sha256(
            definition_payload,
            excluded_top_level_keys=frozenset({"suite_hash"}),
        )
        != expected.hash
        or not suite.required
        or not definition.required
        or len(case_codes) != len(set(case_codes))
        or suite.expected_case_set_hash != definition.expected_case_set_hash
        or suite.executed_case_set_hash != executed_case_set_hash
        or executed_case_set_hash != definition.expected_case_set_hash
        or canonical_sha256(payload) != suite_evidence.artifact_ref.hash
    )


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
    receipt_state_invalid = (
        evidence.execution_status is ExecutionStatus.COMPLETED
        and evidence.decision_status
        not in {
            DecisionStatus.PASS,
            DecisionStatus.FAIL,
            DecisionStatus.INCONCLUSIVE,
        }
    ) or (evidence.execution_status is not ExecutionStatus.COMPLETED and evidence.decision_status is not None)
    if receipt_state_invalid:
        reasons.add(f"REQUIRED_RECEIPT_STATE_INVALID:{expected.id}")
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
    reason, status, decision = _paired_case_outcome(policy, member, paired)
    if reason is not None:
        reasons.add(reason)
        members[index] = member.model_copy(update={"execution_status": status, "decision_status": decision})
    return members


def _paired_case_outcome(
    policy: ReleaseGatePolicy,
    member: RequiredGateMember,
    paired: PairedCaseEvidence | None,
) -> tuple[str | None, ExecutionStatus, DecisionStatus | None]:
    required_receipt_id = policy.paired_comparison_receipt_id
    assert required_receipt_id is not None
    if paired is None:
        return "PAIRED_COMPARISON_EVIDENCE_MISSING", ExecutionStatus.NOT_EVALUATED, None
    if paired.receipt_id != required_receipt_id:
        return "PAIRED_COMPARISON_RECEIPT_MISMATCH", ExecutionStatus.INVALID, None
    if paired.receipt_hash != member.member_hash:
        return "PAIRED_COMPARISON_HASH_MISMATCH", ExecutionStatus.INVALID, None
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
        return "PAIRED_CASE_SET_MISMATCH", ExecutionStatus.INVALID, None
    if not controls_match:
        return "PAIRED_CONTROL_SETTING_MISMATCH", ExecutionStatus.INVALID, None
    if not paired.paired_delta_refs:
        return "PAIRED_DELTA_OR_CI_MISSING", ExecutionStatus.COMPLETED, DecisionStatus.INCONCLUSIVE
    delta_keys = [(item.id, item.version) for item in paired.paired_delta_refs]
    if len(delta_keys) != len(set(delta_keys)):
        return "PAIRED_DELTA_OR_CI_DUPLICATE", ExecutionStatus.INVALID, None
    if paired_case_manifest_hash(paired) != paired.receipt_hash:
        return "PAIRED_COMPARISON_CONTENT_HASH_MISMATCH", ExecutionStatus.INVALID, None
    return None, member.execution_status, member.decision_status


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
    receipts.extend(_profile_members(policy, evidence, reasons))
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

    if gate.aggregate_decision_status is DecisionStatus.PASS and gate.blocking_reason_codes:
        return 2
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
