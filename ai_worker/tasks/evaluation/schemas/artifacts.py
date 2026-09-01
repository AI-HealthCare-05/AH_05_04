from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, StrictBool, TypeAdapter, model_validator

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.authoring import GitCommitSha
from ai_worker.tasks.evaluation.schemas.common import (
    ActorRef,
    CanonicalDecimal,
    CanonicalUuid,
    DecisionStatus,
    DecisionStatusValue,
    ExecutionStatus,
    ExecutionStatusValue,
    ExperimentType,
    ImmutableReference,
    NonEmptyString,
    Partition,
    ResourcePath,
    SafeInteger,
    SemanticVersion,
    Sha256Hex,
    StrictContractModel,
    TaskType,
    UtcTimestamp,
)


def _enum_from_wire(enum_type: type[StrEnum], value: object) -> object:
    if isinstance(value, str):
        return enum_type(value)
    return value


def _tuple_from_wire(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be")


def _require_sorted_unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) != len(set(values)) or list(values) != sorted(values, key=_utf16_key):
        raise ValueError("values must be unique and sorted")
    return values


def _validate_safe_summary(value: str) -> str:
    validate_privacy_boundary({"summary": value})
    return value


PartitionValue = Annotated[Partition, BeforeValidator(lambda value: _enum_from_wire(Partition, value))]
TaskTypeValue = Annotated[TaskType, BeforeValidator(lambda value: _enum_from_wire(TaskType, value))]
ExperimentTypeValue = Annotated[
    ExperimentType,
    BeforeValidator(lambda value: _enum_from_wire(ExperimentType, value)),
]
NonNegativeSafeInteger = Annotated[SafeInteger, Field(ge=0)]
SortedStrings = Annotated[
    tuple[NonEmptyString, ...],
    BeforeValidator(_tuple_from_wire),
    AfterValidator(_require_sorted_unique_strings),
]
EvidenceIds = Annotated[tuple[NonEmptyString, ...], BeforeValidator(_tuple_from_wire)]
OptionalEvidenceIds = EvidenceIds | None
SafeSummary = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=500),
    AfterValidator(_validate_safe_summary),
]


class RuntimeEnvironment(StrEnum):
    LOCAL = "LOCAL"
    CI = "CI"


RuntimeEnvironmentValue = Annotated[
    RuntimeEnvironment,
    BeforeValidator(lambda value: _enum_from_wire(RuntimeEnvironment, value)),
]


class CandidateGuardDecision(StrEnum):
    PASS = "PASS"


CandidateGuardDecisionValue = Annotated[
    CandidateGuardDecision,
    BeforeValidator(lambda value: _enum_from_wire(CandidateGuardDecision, value)),
]


class RuntimeExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    NO_RESULT = "NO_RESULT"
    TIMED_OUT = "TIMED_OUT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class RuntimeReleaseDecision(StrEnum):
    PASS = "PASS"
    LIMITED = "LIMITED"
    REJECTED = "REJECTED"
    STALE = "STALE"


RuntimeExecutionStatusValue = Annotated[
    RuntimeExecutionStatus,
    BeforeValidator(lambda value: _enum_from_wire(RuntimeExecutionStatus, value)),
]
RuntimeReleaseDecisionValue = Annotated[
    RuntimeReleaseDecision,
    BeforeValidator(lambda value: _enum_from_wire(RuntimeReleaseDecision, value)),
]


_BLOCKING_STATUS_ORDER = {
    ExecutionStatus.NOT_IMPLEMENTED: 0,
    ExecutionStatus.NOT_EVALUATED: 1,
    ExecutionStatus.INVALID: 2,
    ExecutionStatus.ERROR: 3,
}


def _validate_blocking_statuses(
    statuses: tuple[ExecutionStatusValue, ...],
) -> tuple[ExecutionStatusValue, ...]:
    if any(status is ExecutionStatus.COMPLETED for status in statuses):
        raise ValueError("COMPLETED is not a blocking status")
    if len(statuses) != len(set(statuses)):
        raise ValueError("blocking statuses must be unique")
    if list(statuses) != sorted(statuses, key=lambda status: _BLOCKING_STATUS_ORDER[status]):
        raise ValueError("blocking statuses must use fixed priority order")
    return statuses


BlockingStatuses = Annotated[
    tuple[ExecutionStatusValue, ...],
    BeforeValidator(_tuple_from_wire),
    AfterValidator(_validate_blocking_statuses),
]


class ResultEnvelope(StrictContractModel):
    schema_id: NonEmptyString
    schema_version: Literal["1.0.0"]
    run_id: CanonicalUuid


class RagEvaluationRun(ResultEnvelope):
    schema_id: Literal["rag-eval.run"]
    experiment_id: NonEmptyString
    variant_id: NonEmptyString
    experiment_type: ExperimentTypeValue
    task_types: Annotated[tuple[TaskTypeValue, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    evaluation_profile_ref: ImmutableReference
    comparison_policy_ref: ImmutableReference
    evaluation_policy_ref: ImmutableReference
    artifact_schema_set_ref: ImmutableReference
    dataset_code: NonEmptyString
    dataset_version: SemanticVersion
    dataset_manifest_sha256: Sha256Hex
    resource_set_hash: Sha256Hex
    evidence_mapping_manifest_sha256: Sha256Hex
    critical_claim_rubric_ref: ImmutableReference
    fixture_git_commit_sha: GitCommitSha | None
    protected_artifact_receipt_ref: ImmutableReference | None
    resolved_evaluation_config_hash: Sha256Hex
    upstream_contract_manifest_hash: Sha256Hex
    retrieval_variant_manifest_hash: Sha256Hex | None
    answer_variant_manifest_hash: Sha256Hex | None
    model_config_hash: Sha256Hex
    prompt_version: NonEmptyString
    evaluated_partitions: Annotated[
        tuple[PartitionValue, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    partition_manifest_hash: Sha256Hex
    environment: RuntimeEnvironmentValue
    runtime_eligible: StrictBool
    candidate_bundle_id: NonEmptyString | None
    candidate_bundle_manifest_hash: Sha256Hex | None
    candidate_guard_decision_id: NonEmptyString | None
    candidate_guard_decision: CandidateGuardDecisionValue | None
    required_case_guard_coverage_manifest_hash: Sha256Hex | None
    executed_by: ActorRef
    started_at: UtcTimestamp
    completed_at: UtcTimestamp | None
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None
    blocking_execution_statuses: BlockingStatuses
    result_content_manifest_hash: Sha256Hex | None

    @model_validator(mode="after")
    def validate_run_state(self) -> RagEvaluationRun:
        completed_fields = (self.completed_at, self.decision_status, self.result_content_manifest_hash)
        if self.execution_status is ExecutionStatus.COMPLETED:
            if any(value is None for value in completed_fields):
                raise ValueError("completed run requires completion time, decision, and content manifest hash")
        elif any(value is not None for value in completed_fields):
            raise ValueError("incomplete run must not carry completion-only fields")

        task_values = [task.value for task in self.task_types]
        if len(task_values) != len(set(task_values)) or task_values != sorted(task_values, key=_utf16_key):
            raise ValueError("task types must be unique and sorted")
        partition_values = [partition.value for partition in self.evaluated_partitions]
        if len(partition_values) != len(set(partition_values)) or partition_values != sorted(
            partition_values, key=_utf16_key
        ):
            raise ValueError("evaluated partitions must be unique and sorted")

        runtime_fields = (
            self.candidate_bundle_id,
            self.candidate_bundle_manifest_hash,
            self.candidate_guard_decision_id,
            self.candidate_guard_decision,
            self.required_case_guard_coverage_manifest_hash,
        )
        if self.runtime_eligible:
            if (
                self.experiment_type is not ExperimentType.END_TO_END_RAG
                or self.environment is not RuntimeEnvironment.LOCAL
            ):
                raise ValueError("runtime-eligible runs must be local END_TO_END_RAG runs")
            if any(value is None for value in runtime_fields):
                raise ValueError("runtime-eligible runs require complete local guard bindings")
        elif any(value is not None for value in runtime_fields):
            raise ValueError("non-runtime runs must not carry guard bindings")
        return self


class _CaseResultBase(ResultEnvelope):
    schema_id: Literal["rag-eval.case-result"]
    case_id: NonEmptyString
    dataset_code: NonEmptyString
    dataset_version: SemanticVersion
    partition: PartitionValue
    input_sha256: Sha256Hex
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None
    failure_codes: SortedStrings
    retrieved_evidence_ids: OptionalEvidenceIds
    selected_evidence_ids: OptionalEvidenceIds
    actual_claim_ids: OptionalEvidenceIds
    actual_citation_evidence_ids: OptionalEvidenceIds
    actual_rule_ids: OptionalEvidenceIds
    actual_scope_codes: OptionalEvidenceIds
    actual_response_level: NonEmptyString | None
    actual_safety_disposition: NonEmptyString | None
    actual_execution_status: RuntimeExecutionStatusValue | None
    actual_release_decision: RuntimeReleaseDecisionValue | None
    actual_fallback_code: NonEmptyString | None
    actual_provider_invocation: StrictBool | None
    actual_retrieval_invocation: StrictBool | None
    actual_publication_allowed: StrictBool | None
    answer_sha256: Sha256Hex | None
    latency_ms: NonNegativeSafeInteger | None
    input_token_count: NonNegativeSafeInteger | None
    output_token_count: NonNegativeSafeInteger | None
    estimated_cost: CanonicalDecimal | None

    @model_validator(mode="after")
    def validate_execution_state(self) -> _CaseResultBase:
        if self.execution_status is ExecutionStatus.COMPLETED:
            if self.decision_status is None:
                raise ValueError("completed case requires a decision")
        elif self.decision_status is not None:
            raise ValueError("incomplete case must not carry a decision")
        return self


class RetrievalCaseResult(_CaseResultBase):
    task_type: Literal[TaskType.RETRIEVAL]
    retrieved_evidence_ids: EvidenceIds
    selected_evidence_ids: EvidenceIds
    actual_claim_ids: None
    actual_citation_evidence_ids: None
    actual_rule_ids: None
    actual_scope_codes: None
    actual_response_level: None
    actual_safety_disposition: None
    actual_execution_status: None
    actual_release_decision: None
    actual_fallback_code: None
    actual_provider_invocation: None
    actual_retrieval_invocation: StrictBool
    actual_publication_allowed: None
    answer_sha256: None
    input_token_count: None
    output_token_count: None
    estimated_cost: None


class _AnswerCaseResultBase(_CaseResultBase):
    retrieved_evidence_ids: OptionalEvidenceIds
    selected_evidence_ids: OptionalEvidenceIds
    actual_claim_ids: EvidenceIds
    actual_citation_evidence_ids: EvidenceIds
    actual_rule_ids: OptionalEvidenceIds
    actual_scope_codes: OptionalEvidenceIds
    actual_response_level: None
    actual_safety_disposition: None
    actual_execution_status: None
    actual_release_decision: None
    actual_fallback_code: None
    actual_provider_invocation: None
    actual_retrieval_invocation: StrictBool | None
    actual_publication_allowed: None
    answer_sha256: Sha256Hex


class AnswerQualityCaseResult(_AnswerCaseResultBase):
    task_type: Literal[TaskType.ANSWER_QUALITY]


class AnswerGroundingCaseResult(_AnswerCaseResultBase):
    task_type: Literal[TaskType.ANSWER_GROUNDING]


class _SafetyCaseResultBase(_CaseResultBase):
    retrieved_evidence_ids: OptionalEvidenceIds
    selected_evidence_ids: OptionalEvidenceIds
    actual_claim_ids: EvidenceIds
    actual_citation_evidence_ids: EvidenceIds
    actual_rule_ids: EvidenceIds
    actual_scope_codes: EvidenceIds
    actual_response_level: NonEmptyString
    actual_safety_disposition: NonEmptyString
    actual_execution_status: RuntimeExecutionStatusValue
    actual_release_decision: RuntimeReleaseDecisionValue
    actual_fallback_code: NonEmptyString | None
    actual_provider_invocation: StrictBool
    actual_retrieval_invocation: StrictBool
    actual_publication_allowed: StrictBool
    answer_sha256: Sha256Hex | None


class SafetyCaseResult(_SafetyCaseResultBase):
    task_type: Literal[TaskType.SAFETY]


class EndToEndRagCaseResult(_SafetyCaseResultBase):
    task_type: Literal[TaskType.END_TO_END_RAG]


CaseResult = Annotated[
    RetrievalCaseResult
    | AnswerQualityCaseResult
    | AnswerGroundingCaseResult
    | SafetyCaseResult
    | EndToEndRagCaseResult,
    Field(discriminator="task_type"),
]
CASE_RESULT_ADAPTER: TypeAdapter[CaseResult] = TypeAdapter(CaseResult)


class MetricResult(StrictContractModel):
    metric_id: NonEmptyString
    metric_version: SemanticVersion
    partition: PartitionValue
    slice_id: NonEmptyString
    required: StrictBool
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None
    sample_case_count: NonNegativeSafeInteger | None
    sample_independent_group_count: NonNegativeSafeInteger | None
    numerator: NonNegativeSafeInteger | None
    denominator: NonNegativeSafeInteger | None
    metric_value: CanonicalDecimal | None
    unit_of_analysis: NonEmptyString
    estimator_id: NonEmptyString
    estimator_version: SemanticVersion
    independence_unit: NonEmptyString | None
    cluster_dimension: NonEmptyString | None
    ci_lower: CanonicalDecimal | None
    ci_upper: CanonicalDecimal | None
    ci_method_id: NonEmptyString | None
    ci_method_version: SemanticVersion | None
    ci_level: CanonicalDecimal | None
    ci_sidedness: NonEmptyString | None
    threshold: CanonicalDecimal | None
    reason_code: NonEmptyString | None

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.metric_id, self.partition.value, self.slice_id)

    @model_validator(mode="after")
    def validate_metric_state(self) -> MetricResult:
        calculated = (
            self.sample_case_count,
            self.sample_independent_group_count,
            self.numerator,
            self.denominator,
            self.metric_value,
            self.ci_lower,
            self.ci_upper,
            self.reason_code,
        )
        if self.execution_status is not ExecutionStatus.COMPLETED:
            if self.decision_status is not None or any(value is not None for value in calculated):
                raise ValueError("incomplete metrics must not carry decisions or calculated values")
            return self
        if self.decision_status is None:
            raise ValueError("completed metrics require a decision")
        if self.required and self.decision_status is DecisionStatus.NOT_APPLICABLE:
            raise ValueError("required metrics cannot be not applicable")
        if any(
            value is None
            for value in (
                self.sample_case_count,
                self.sample_independent_group_count,
                self.numerator,
                self.denominator,
            )
        ):
            raise ValueError("completed metrics require sample and ratio counts")
        if self.decision_status is DecisionStatus.INCONCLUSIVE and self.reason_code is None:
            raise ValueError("inconclusive metrics require a reason code")
        return self


class MetricResults(ResultEnvelope):
    schema_id: Literal["rag-eval.metrics"]
    metrics: Annotated[tuple[MetricResult, ...], BeforeValidator(_tuple_from_wire)]

    @model_validator(mode="after")
    def require_sorted_metrics(self) -> MetricResults:
        keys = [item.sort_key for item in self.metrics]
        if len(keys) != len(set(keys)) or keys != sorted(keys):
            raise ValueError("metrics must be unique and sorted by metric, partition, and slice")
        return self


class SuiteCaseResult(StrictContractModel):
    case_code: NonEmptyString
    case_input_hash: Sha256Hex
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None
    artifact_ref: ImmutableReference | None
    failure_code: NonEmptyString | None

    @model_validator(mode="after")
    def validate_state(self) -> SuiteCaseResult:
        if self.execution_status is ExecutionStatus.COMPLETED:
            if self.decision_status is None:
                raise ValueError("completed suite cases require a decision")
        elif self.decision_status is not None:
            raise ValueError("incomplete suite cases must not carry a decision")
        return self


class SuiteResults(ResultEnvelope):
    schema_id: Literal["rag-eval.suite-results"]
    suite_id: NonEmptyString
    suite_version: SemanticVersion
    suite_definition_hash: Sha256Hex
    required: StrictBool
    expected_case_set_hash: Sha256Hex
    executed_case_set_hash: Sha256Hex
    case_results: Annotated[tuple[SuiteCaseResult, ...], BeforeValidator(_tuple_from_wire)]
    aggregate_execution_status: ExecutionStatusValue
    aggregate_decision_status: DecisionStatusValue | None
    blocking_execution_statuses: BlockingStatuses
    artifact_hash: Sha256Hex | None

    @model_validator(mode="after")
    def validate_aggregate(self) -> SuiteResults:
        execution_statuses = [item.execution_status for item in self.case_results]
        _validate_aggregate_state(
            execution_statuses,
            [item.decision_status for item in self.case_results],
            self.aggregate_execution_status,
            self.aggregate_decision_status,
            self.required,
        )
        _validate_blocking_aggregation(
            execution_statuses,
            self.aggregate_execution_status,
            self.blocking_execution_statuses,
        )
        return self


class ControlledVariableCheck(StrictContractModel):
    variable_key: NonEmptyString
    baseline_value_hash: Sha256Hex
    candidate_value_hash: Sha256Hex
    matched: StrictBool


class ComparisonDecision(StrEnum):
    IMPROVED = "IMPROVED"
    NON_INFERIOR = "NON_INFERIOR"
    REGRESSED = "REGRESSED"
    INCONCLUSIVE = "INCONCLUSIVE"


ComparisonDecisionValue = Annotated[
    ComparisonDecision,
    BeforeValidator(lambda value: _enum_from_wire(ComparisonDecision, value)),
]


class ScopeComparison(StrictContractModel):
    metric_id: NonEmptyString
    partition: PartitionValue
    slice_id: NonEmptyString
    baseline_value: CanonicalDecimal | None
    candidate_value: CanonicalDecimal | None
    absolute_delta: CanonicalDecimal | None
    relative_delta: CanonicalDecimal | None
    paired_test_method: NonEmptyString | None
    p_value: CanonicalDecimal | None
    comparison_decision: ComparisonDecisionValue


class ComparisonResult(ResultEnvelope):
    schema_id: Literal["rag-eval.comparison"]
    experiment_id: NonEmptyString
    baseline_run_id: CanonicalUuid
    baseline_run_hash: Sha256Hex
    candidate_run_id: CanonicalUuid
    candidate_run_hash: Sha256Hex
    controlled_variable_checks: Annotated[tuple[ControlledVariableCheck, ...], BeforeValidator(_tuple_from_wire)]
    scope_comparisons: Annotated[tuple[ScopeComparison, ...], BeforeValidator(_tuple_from_wire)]
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None

    @model_validator(mode="after")
    def validate_state(self) -> ComparisonResult:
        if self.execution_status is ExecutionStatus.COMPLETED:
            if self.decision_status is None:
                raise ValueError("completed comparisons require an evaluation decision")
        elif self.decision_status is not None:
            raise ValueError("incomplete comparisons must not carry a decision")
        return self


class GateMemberType(StrEnum):
    METRIC = "METRIC"
    SUITE = "SUITE"
    CONTRACT_RECEIPT = "CONTRACT_RECEIPT"


GateMemberTypeValue = Annotated[
    GateMemberType,
    BeforeValidator(lambda value: _enum_from_wire(GateMemberType, value)),
]


class RequiredGateMember(StrictContractModel):
    member_type: GateMemberTypeValue
    member_id: NonEmptyString
    member_version: SemanticVersion
    member_hash: Sha256Hex
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None
    receipt_or_artifact_ref: ImmutableReference | None

    @model_validator(mode="after")
    def validate_state(self) -> RequiredGateMember:
        if self.execution_status is ExecutionStatus.COMPLETED:
            if self.decision_status is None or self.decision_status is DecisionStatus.NOT_APPLICABLE:
                raise ValueError("required completed members require PASS, FAIL, or INCONCLUSIVE")
        elif self.decision_status is not None:
            raise ValueError("incomplete required members must not carry a decision")
        return self


class GateResult(ResultEnvelope):
    schema_id: Literal["rag-eval.gate"]
    evaluation_policy_ref: ImmutableReference
    evaluation_profile_ref: ImmutableReference
    comparison_policy_ref: ImmutableReference
    required_scope_manifest_hash: Sha256Hex
    required_metrics: Annotated[tuple[RequiredGateMember, ...], BeforeValidator(_tuple_from_wire)]
    required_suites: Annotated[tuple[RequiredGateMember, ...], BeforeValidator(_tuple_from_wire)]
    required_contract_receipts: Annotated[tuple[RequiredGateMember, ...], BeforeValidator(_tuple_from_wire)]
    aggregate_execution_status: ExecutionStatusValue
    aggregate_decision_status: DecisionStatusValue | None
    blocking_execution_statuses: BlockingStatuses
    blocking_reason_codes: SortedStrings

    @model_validator(mode="after")
    def validate_required_aggregation(self) -> GateResult:
        groups = (
            (self.required_metrics, GateMemberType.METRIC),
            (self.required_suites, GateMemberType.SUITE),
            (self.required_contract_receipts, GateMemberType.CONTRACT_RECEIPT),
        )
        members: list[RequiredGateMember] = []
        for group, expected_type in groups:
            if any(member.member_type is not expected_type for member in group):
                raise ValueError("gate member type must match its required-member collection")
            members.extend(group)
        execution_statuses = [member.execution_status for member in members]
        _validate_aggregate_state(
            execution_statuses,
            [member.decision_status for member in members],
            self.aggregate_execution_status,
            self.aggregate_decision_status,
            True,
        )
        _validate_blocking_aggregation(
            execution_statuses,
            self.aggregate_execution_status,
            self.blocking_execution_statuses,
        )
        return self


def _validate_aggregate_state(
    execution_statuses: list[ExecutionStatus],
    decision_statuses: list[DecisionStatus | None],
    aggregate_execution_status: ExecutionStatus,
    aggregate_decision_status: DecisionStatus | None,
    required: bool,
) -> None:
    if all(status is ExecutionStatus.COMPLETED for status in execution_statuses):
        if aggregate_execution_status is not ExecutionStatus.COMPLETED or aggregate_decision_status is None:
            raise ValueError("fully completed members require a completed aggregate decision")
        if required and aggregate_decision_status is DecisionStatus.NOT_APPLICABLE:
            raise ValueError("required aggregate cannot be not applicable")
        expected = DecisionStatus.PASS
        if DecisionStatus.FAIL in decision_statuses:
            expected = DecisionStatus.FAIL
        elif DecisionStatus.INCONCLUSIVE in decision_statuses:
            expected = DecisionStatus.INCONCLUSIVE
        elif decision_statuses and all(status is DecisionStatus.NOT_APPLICABLE for status in decision_statuses):
            expected = DecisionStatus.NOT_APPLICABLE
        if aggregate_decision_status is not expected:
            raise ValueError("aggregate decision does not match member precedence")
    elif aggregate_execution_status is ExecutionStatus.COMPLETED or aggregate_decision_status is not None:
        raise ValueError("incomplete members require a blocking aggregate with null decision")


def _validate_blocking_aggregation(
    execution_statuses: list[ExecutionStatus],
    aggregate_execution_status: ExecutionStatus,
    blocking_execution_statuses: tuple[ExecutionStatus, ...],
) -> None:
    expected = tuple(
        sorted(
            {status for status in execution_statuses if status is not ExecutionStatus.COMPLETED},
            key=lambda status: _BLOCKING_STATUS_ORDER[status],
        )
    )
    if blocking_execution_statuses != expected:
        raise ValueError("blocking statuses must contain every incomplete member state")
    if expected and aggregate_execution_status not in expected:
        raise ValueError("aggregate execution status must identify a blocking member state")


class FailureRecord(ResultEnvelope):
    schema_id: Literal["rag-eval.failure"]
    case_id: NonEmptyString
    failure_code: NonEmptyString
    failure_stage: NonEmptyString
    expected_summary: SafeSummary
    actual_summary: SafeSummary
    root_cause_code: NonEmptyString | None
    followup_issue_ref: NonEmptyString | None
    created_at: UtcTimestamp


_CONTENT_FILENAMES = frozenset(
    {
        "cases.jsonl",
        "metrics.json",
        "suite-results.json",
        "comparison.json",
        "gate.json",
        "failures.jsonl",
        "report.md",
    }
)


class ContentArtifact(StrictContractModel):
    relative_path: ResourcePath
    sha256: Sha256Hex
    size_bytes: NonNegativeSafeInteger

    @model_validator(mode="after")
    def require_result_payload_filename(self) -> ContentArtifact:
        if self.relative_path not in _CONTENT_FILENAMES:
            raise ValueError("content manifest contains a disallowed or self-referential artifact")
        return self


class ContentManifest(ResultEnvelope):
    schema_id: Literal["rag-eval.content-manifest"]
    hash_algorithm: Literal["SHA-256"]
    artifacts: Annotated[tuple[ContentArtifact, ...], BeforeValidator(_tuple_from_wire)]
    artifact_count: NonNegativeSafeInteger
    manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_manifest(self) -> ContentManifest:
        paths = [artifact.relative_path for artifact in self.artifacts]
        if len(paths) != len(set(paths)) or paths != sorted(paths, key=_utf16_key):
            raise ValueError("content artifacts must be unique and sorted by relative path")
        if self.artifact_count != len(self.artifacts):
            raise ValueError("artifact count must be derived from content entries")
        payload = self.model_dump(mode="json")
        if self.manifest_sha256 != canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"})):
            raise ValueError("content manifest self hash does not match")
        return self


class ValidationReceipt(StrictContractModel):
    schema_id: Literal["rag-eval.validation-receipt"]
    schema_version: Literal["1.0.0"]
    validation_id: CanonicalUuid
    validated_at: UtcTimestamp
    validator_version: SemanticVersion
    manifest_path: ResourcePath
    dataset_code: NonEmptyString
    dataset_version: SemanticVersion
    dataset_manifest_sha256: Sha256Hex | None
    evaluation_profile_ref: ImmutableReference | None
    comparison_policy_ref: ImmutableReference | None
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None
    release_eligible: Literal[False]
    error_codes: SortedStrings
    invalid_resource_paths: Annotated[
        tuple[ResourcePath, ...],
        BeforeValidator(_tuple_from_wire),
        AfterValidator(_require_sorted_unique_strings),
    ]

    @model_validator(mode="after")
    def validate_validation_outcome(self) -> ValidationReceipt:
        allowed = {
            (ExecutionStatus.COMPLETED, DecisionStatus.NOT_APPLICABLE),
            (ExecutionStatus.INVALID, None),
            (ExecutionStatus.ERROR, None),
        }
        if (self.execution_status, self.decision_status) not in allowed:
            raise ValueError("validation receipt outcome is not permitted")
        return self


RESULT_ARTIFACT_MODELS: dict[str, type[StrictContractModel] | TypeAdapter[CaseResult]] = {
    "rag-eval.run": RagEvaluationRun,
    "rag-eval.case-result": CASE_RESULT_ADAPTER,
    "rag-eval.metrics": MetricResults,
    "rag-eval.suite-results": SuiteResults,
    "rag-eval.comparison": ComparisonResult,
    "rag-eval.gate": GateResult,
    "rag-eval.failure": FailureRecord,
    "rag-eval.content-manifest": ContentManifest,
}
