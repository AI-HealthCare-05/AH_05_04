from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, TypeAdapter, model_validator

from ai_worker.tasks.evaluation.schemas.authoring import (
    AnswerGroundingExpected,
    AnswerQualityExpected,
    CaseResource,
    ContentClassificationValue,
    DatasetStatus,
    DatasetStatusValue,
    GitCommitSha,
    LeakageGroupIds,
    MedicationFixture,
    NonEmptyText,
    PartitionCounts,
    PartitionValue,
    PatientContextFixture,
    PrescriptionFixture,
    QueryText,
    RetrievalExpected,
    RuntimeFixture,
    SafetyDisposition,
    SafetyExpected,
    StableIds,
    TaskTypeValue,
    _enum_from_wire,
    _require_sorted_unique,
    _require_team_approval_role,
    _tuple_from_wire,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ActorRole,
    ContentClassification,
    ImmutableReference,
    ReviewProvenance,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
    TaskType,
    TeamGoldStatus,
    UtcTimestamp,
)


class SourceEligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    EXPIRED = "EXPIRED"
    INACTIVE = "INACTIVE"
    CONFLICTING = "CONFLICTING"


class BundleEligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    SOURCE_INELIGIBLE = "SOURCE_INELIGIBLE"
    SCOPE_INELIGIBLE = "SCOPE_INELIGIBLE"
    MEMBER_INELIGIBLE = "MEMBER_INELIGIBLE"


class DependencyFault(StrEnum):
    NONE = "NONE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"


class RuleExpectedOutcome(StrEnum):
    MATCHED_RULES = "MATCHED_RULES"
    NO_MATCH = "NO_MATCH"
    NOT_INVOKED = "NOT_INVOKED"


class RuleNotInvokedReason(StrEnum):
    SAFETY_ROUTED = "SAFETY_ROUTED"
    SOURCE_INELIGIBLE = "SOURCE_INELIGIBLE"
    BUNDLE_INELIGIBLE = "BUNDLE_INELIGIBLE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"


SourceEligibilityStatusValue = Annotated[
    SourceEligibilityStatus,
    BeforeValidator(lambda value: _enum_from_wire(SourceEligibilityStatus, value)),
]
BundleEligibilityStatusValue = Annotated[
    BundleEligibilityStatus,
    BeforeValidator(lambda value: _enum_from_wire(BundleEligibilityStatus, value)),
]
DependencyFaultValue = Annotated[
    DependencyFault,
    BeforeValidator(lambda value: _enum_from_wire(DependencyFault, value)),
]
RuleExpectedOutcomeValue = Annotated[
    RuleExpectedOutcome,
    BeforeValidator(lambda value: _enum_from_wire(RuleExpectedOutcome, value)),
]
RuleNotInvokedReasonValue = Annotated[
    RuleNotInvokedReason,
    BeforeValidator(lambda value: _enum_from_wire(RuleNotInvokedReason, value)),
]


class RuntimeFixtureV11(RuntimeFixture):
    source_eligibility_status: SourceEligibilityStatusValue
    bundle_eligibility_status: BundleEligibilityStatusValue
    dependency_fault: DependencyFaultValue

    @model_validator(mode="after")
    def validate_eligibility_axes(self) -> RuntimeFixtureV11:
        source_eligible = self.source_eligibility_status is SourceEligibilityStatus.ELIGIBLE
        bundle_reports_source = self.bundle_eligibility_status is BundleEligibilityStatus.SOURCE_INELIGIBLE
        if source_eligible == bundle_reports_source:
            raise ValueError("Source and Bundle eligibility axes are contradictory")
        return self


class EvaluationContextV11(StrictContractModel):
    prescription_fixture: PrescriptionFixture | None
    medication_fixtures: Annotated[
        tuple[MedicationFixture, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    patient_context_fixture: PatientContextFixture | None
    runtime_fixture: RuntimeFixtureV11 | None

    @model_validator(mode="after")
    def validate_medications(self) -> EvaluationContextV11:
        keys = [item.medication_fixture_id for item in self.medication_fixtures]
        if len(keys) != len(set(keys)):
            raise ValueError("medication fixture IDs must be unique")
        return self


class RetrievalExpectedV11(RetrievalExpected):
    expected_rule_outcome: None
    expected_rule_not_invoked_reason: None


class AnswerQualityExpectedV11(AnswerQualityExpected):
    expected_rule_outcome: None
    expected_rule_not_invoked_reason: None


class AnswerGroundingExpectedV11(AnswerGroundingExpected):
    expected_rule_outcome: None
    expected_rule_not_invoked_reason: None


class SafetyExpectedV11(SafetyExpected):
    expected_rule_ids: StableIds
    expected_rule_outcome: RuleExpectedOutcomeValue
    expected_rule_not_invoked_reason: RuleNotInvokedReasonValue | None

    @model_validator(mode="after")
    def validate_rule_cardinality(self) -> SafetyExpectedV11:
        has_rules = bool(self.expected_rule_ids)
        if self.expected_rule_outcome is RuleExpectedOutcome.MATCHED_RULES:
            if not has_rules or self.expected_rule_not_invoked_reason is not None:
                raise ValueError("MATCHED_RULES requires Rule IDs and no not-invoked reason")
        elif self.expected_rule_outcome is RuleExpectedOutcome.NO_MATCH:
            if has_rules or self.expected_rule_not_invoked_reason is not None:
                raise ValueError("NO_MATCH requires empty Rule IDs and no not-invoked reason")
        elif has_rules or self.expected_rule_not_invoked_reason is None:
            raise ValueError("NOT_INVOKED requires empty Rule IDs and a typed reason")
        return self


class EndToEndRagExpectedV11(SafetyExpectedV11):
    pass


def _validate_no_match(runtime: RuntimeFixtureV11) -> None:
    if (
        runtime.source_eligibility_status is not SourceEligibilityStatus.ELIGIBLE
        or runtime.bundle_eligibility_status is not BundleEligibilityStatus.ELIGIBLE
        or runtime.dependency_fault is not DependencyFault.NONE
    ):
        raise ValueError("NO_MATCH requires eligible Rule inputs without dependency fault")


def _validate_not_invoked(expected: SafetyExpectedV11, runtime: RuntimeFixtureV11) -> None:
    reason = expected.expected_rule_not_invoked_reason
    if reason is RuleNotInvokedReason.SAFETY_ROUTED:
        if (
            expected.expected_safety_disposition is SafetyDisposition.NORMAL
            or expected.expected_provider_invocation
            or expected.expected_retrieval_invocation
        ):
            raise ValueError("SAFETY_ROUTED requires a routed disposition and no general pipeline invocation")
    elif (
        reason is RuleNotInvokedReason.SOURCE_INELIGIBLE
        and runtime.source_eligibility_status is SourceEligibilityStatus.ELIGIBLE
    ):
        raise ValueError("SOURCE_INELIGIBLE requires a non-eligible Source")
    elif (
        reason is RuleNotInvokedReason.BUNDLE_INELIGIBLE
        and runtime.bundle_eligibility_status is BundleEligibilityStatus.ELIGIBLE
    ):
        raise ValueError("BUNDLE_INELIGIBLE requires a non-eligible Bundle")
    elif reason is RuleNotInvokedReason.DEPENDENCY_FAILURE and runtime.dependency_fault is DependencyFault.NONE:
        raise ValueError("DEPENDENCY_FAILURE requires a typed dependency fault")


class _CaseBaseV11(StrictContractModel):
    schema_id: Literal["rag-eval.case"]
    schema_version: Literal["1.1.0"]
    case_id: StableId
    dataset_code: StableId
    dataset_version: SemanticVersion
    task_type: TaskTypeValue
    partition: PartitionValue
    slice_ids: StableIds
    data_classification: ContentClassificationValue
    query: QueryText
    context: EvaluationContextV11
    input_sha256: Sha256Hex
    leakage_group_ids: LeakageGroupIds
    critical_claim_rubric_ref: ImmutableReference
    gold_version: SemanticVersion
    review_provenance: ReviewProvenance
    tags: StableIds

    @model_validator(mode="after")
    def validate_sorted_sets(self) -> _CaseBaseV11:
        _require_sorted_unique(self.slice_ids, "slice IDs must be unique and sorted")
        _require_sorted_unique(self.tags, "tags must be unique and sorted")
        return self


class RetrievalCaseV11(_CaseBaseV11):
    task_type: Literal[TaskType.RETRIEVAL]
    expected: RetrievalExpectedV11


class AnswerQualityCaseV11(_CaseBaseV11):
    task_type: Literal[TaskType.ANSWER_QUALITY]
    expected: AnswerQualityExpectedV11


class AnswerGroundingCaseV11(_CaseBaseV11):
    task_type: Literal[TaskType.ANSWER_GROUNDING]
    expected: AnswerGroundingExpectedV11


class _RuleOutcomeCaseV11(_CaseBaseV11):
    expected: SafetyExpectedV11

    @model_validator(mode="after")
    def validate_rule_outcome_context(self) -> _RuleOutcomeCaseV11:
        expected = self.expected
        runtime = self.context.runtime_fixture
        if runtime is None:
            raise ValueError("Safety Rule outcome requires a runtime fixture")
        if expected.expected_rule_outcome is RuleExpectedOutcome.NO_MATCH:
            _validate_no_match(runtime)
        elif expected.expected_rule_outcome is RuleExpectedOutcome.NOT_INVOKED:
            _validate_not_invoked(expected, runtime)
        return self


class SafetyCaseV11(_RuleOutcomeCaseV11):
    task_type: Literal[TaskType.SAFETY]
    expected: SafetyExpectedV11

    @model_validator(mode="after")
    def require_safety_approval_role(self) -> SafetyCaseV11:
        _require_team_approval_role(
            self.review_provenance,
            frozenset({ActorRole.PRODUCT_SAFETY_REVIEWER, ActorRole.MEDICAL_REVIEWER}),
        )
        return self


class EndToEndRagCaseV11(_RuleOutcomeCaseV11):
    task_type: Literal[TaskType.END_TO_END_RAG]
    expected: EndToEndRagExpectedV11

    @model_validator(mode="after")
    def require_safety_approval_role(self) -> EndToEndRagCaseV11:
        _require_team_approval_role(
            self.review_provenance,
            frozenset({ActorRole.PRODUCT_SAFETY_REVIEWER, ActorRole.MEDICAL_REVIEWER}),
        )
        return self


EvaluationCaseV11 = Annotated[
    RetrievalCaseV11 | AnswerQualityCaseV11 | AnswerGroundingCaseV11 | SafetyCaseV11 | EndToEndRagCaseV11,
    Field(discriminator="task_type"),
]
EVALUATION_CASE_ADAPTER_V1_1: TypeAdapter[EvaluationCaseV11] = TypeAdapter(EvaluationCaseV11)


class DatasetManifestV11(StrictContractModel):
    schema_id: Literal["rag-eval.dataset-manifest"]
    schema_version: Literal["1.1.0"]
    dataset_code: StableId
    dataset_version: SemanticVersion
    scope: StableId
    description: Annotated[NonEmptyText, Field(max_length=1000)]
    data_classification: ContentClassificationValue
    deidentification_approval_receipt_ref: ImmutableReference | None
    critical_claim_rubric_ref: ImmutableReference
    evidence_mapping_manifest_sha256: Sha256Hex
    evaluation_corpus_snapshot_ref: ImmutableReference
    case_resources: Annotated[tuple[CaseResource, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    partition_counts: PartitionCounts
    resource_set_hash: Sha256Hex
    fixture_git_commit_sha: GitCommitSha | None
    protected_artifact_receipt_ref: ImmutableReference | None
    status: DatasetStatusValue
    frozen_at: UtcTimestamp | None
    review_provenance: ReviewProvenance
    manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_manifest(self) -> DatasetManifestV11:
        provenance = self.review_provenance
        _require_team_approval_role(provenance, frozenset({ActorRole.DATASET_CUSTODIAN}))
        if (self.fixture_git_commit_sha is None) == (self.protected_artifact_receipt_ref is None):
            raise ValueError("exactly one dataset source provenance is required")
        if (
            self.data_classification is ContentClassification.APPROVED_DEIDENTIFIED
            and self.deidentification_approval_receipt_ref is None
        ):
            raise ValueError("approved deidentified data requires an approval receipt")
        if (
            self.data_classification is ContentClassification.SYNTHETIC
            and self.deidentification_approval_receipt_ref is not None
        ):
            raise ValueError("synthetic data must not claim deidentification approval")
        if self.status is DatasetStatus.FROZEN:
            if self.frozen_at is None or provenance.team_gold_status is not TeamGoldStatus.APPROVED:
                raise ValueError("frozen Dataset requires approved provenance and frozen_at")
        elif self.frozen_at is not None:
            raise ValueError("non-frozen Dataset must not carry frozen_at")
        resource_keys = [(item.partition.value, item.path, item.case_id) for item in self.case_resources]
        if len(resource_keys) != len(set(resource_keys)) or resource_keys != sorted(resource_keys):
            raise ValueError("Case resources must be unique and sorted")
        return self
