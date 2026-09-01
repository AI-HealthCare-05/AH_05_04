from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, StrictBool, StringConstraints, TypeAdapter, model_validator

from ai_worker.tasks.evaluation.schemas.common import (
    ActorRole,
    ContentClassification,
    ImmutableReference,
    Partition,
    ResourcePath,
    ReviewProvenance,
    SafeInteger,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
    TaskType,
    TeamGoldStatus,
    UtcTimestamp,
    validate_schema_value,
)

_GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def _enum_from_wire(enum_type: type[StrEnum], value: object) -> object:
    return enum_type(value) if isinstance(value, str) else value


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _validate_git_sha(value: str) -> str:
    if _GIT_SHA_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a lowercase Git commit SHA")
    return value


def _validate_text(value: str) -> str:
    if value != value.strip() or _CONTROL_CHARACTER.search(value):
        raise ValueError("text must be trimmed and contain no control characters")
    return value


def _validate_synthetic_token(value: str) -> str:
    if not value.startswith("SYNTHETIC_"):
        raise ValueError("synthetic fixture tokens require SYNTHETIC_ prefix")
    return value


def _require_sorted_unique(values: tuple[object, ...], message: str) -> None:
    if len(values) != len(set(values)) or list(values) != sorted(values, key=repr):
        raise ValueError(message)


def _require_team_approval_role(provenance: ReviewProvenance, allowed_roles: frozenset[ActorRole]) -> None:
    if provenance.team_gold_status is not TeamGoldStatus.APPROVED:
        return
    if provenance.approved_by is None or provenance.approved_by.role not in allowed_roles:
        raise ValueError("team gold approval actor role is not permitted for this schema")


GitCommitSha = Annotated[
    str,
    StringConstraints(strict=True, pattern=_GIT_SHA_PATTERN.pattern),
    AfterValidator(_validate_git_sha),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1),
    AfterValidator(_validate_text),
]
QueryText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=2000),
    AfterValidator(_validate_text),
]
SyntheticToken = Annotated[
    str,
    StringConstraints(strict=True, min_length=11, pattern=r"^SYNTHETIC_"),
    AfterValidator(_validate_synthetic_token),
]
PartitionValue = Annotated[Partition, BeforeValidator(lambda value: _enum_from_wire(Partition, value))]
ContentClassificationValue = Annotated[
    ContentClassification,
    BeforeValidator(lambda value: _enum_from_wire(ContentClassification, value)),
]
TaskTypeValue = Annotated[TaskType, BeforeValidator(lambda value: _enum_from_wire(TaskType, value))]
StableIds = Annotated[tuple[StableId, ...], BeforeValidator(_tuple_from_wire)]
NonEmptyStableIds = Annotated[
    tuple[StableId, ...],
    BeforeValidator(_tuple_from_wire),
    Field(min_length=1),
]


class EvidenceType(StrEnum):
    PRESCRIPTION = "PRESCRIPTION"
    KNOWLEDGE_CHUNK = "KNOWLEDGE_CHUNK"
    INTERACTION_RULE = "INTERACTION_RULE"
    LIFESTYLE_GUIDELINE = "LIFESTYLE_GUIDELINE"
    SAFETY_POLICY = "SAFETY_POLICY"


class DatasetStatus(StrEnum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    RETIRED = "RETIRED"


class PregnancyStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"
    SYNTHETIC_TRUE = "SYNTHETIC_TRUE"
    SYNTHETIC_FALSE = "SYNTHETIC_FALSE"


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


class Criticality(StrEnum):
    CRITICAL = "CRITICAL"
    NON_CRITICAL = "NON_CRITICAL"


class ResponseLevel(StrEnum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"
    UNKNOWN = "UNKNOWN"


class SafetyDisposition(StrEnum):
    NORMAL = "NORMAL"
    URGENT_ROUTED = "URGENT_ROUTED"
    EMERGENCY_ROUTED = "EMERGENCY_ROUTED"
    UNKNOWN_RISK = "UNKNOWN_RISK"
    BLOCKED_ACTION = "BLOCKED_ACTION"


class FallbackCode(StrEnum):
    NO_APPROVED_EVIDENCE = "NO_APPROVED_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    SAFETY_ROUTED = "SAFETY_ROUTED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PRESCRIPTION_STALE = "PRESCRIPTION_STALE"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


class RiskLevel(StrEnum):
    GENERAL = "GENERAL"
    PROFESSIONAL_CHECK = "PROFESSIONAL_CHECK"
    URGENT = "URGENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class EvidenceTargetKind(StrEnum):
    RUNTIME_TYPED_REF = "RUNTIME_TYPED_REF"
    FIXTURE_RECORD = "FIXTURE_RECORD"


EvidenceTypeValue = Annotated[EvidenceType, BeforeValidator(lambda value: _enum_from_wire(EvidenceType, value))]
DatasetStatusValue = Annotated[
    DatasetStatus,
    BeforeValidator(lambda value: _enum_from_wire(DatasetStatus, value)),
]
PregnancyStatusValue = Annotated[
    PregnancyStatus,
    BeforeValidator(lambda value: _enum_from_wire(PregnancyStatus, value)),
]
RuntimeExecutionStatusValue = Annotated[
    RuntimeExecutionStatus,
    BeforeValidator(lambda value: _enum_from_wire(RuntimeExecutionStatus, value)),
]
RuntimeReleaseDecisionValue = Annotated[
    RuntimeReleaseDecision,
    BeforeValidator(lambda value: _enum_from_wire(RuntimeReleaseDecision, value)),
]
CriticalityValue = Annotated[Criticality, BeforeValidator(lambda value: _enum_from_wire(Criticality, value))]
ResponseLevelValue = Annotated[ResponseLevel, BeforeValidator(lambda value: _enum_from_wire(ResponseLevel, value))]
SafetyDispositionValue = Annotated[
    SafetyDisposition,
    BeforeValidator(lambda value: _enum_from_wire(SafetyDisposition, value)),
]
FallbackCodeValue = Annotated[FallbackCode, BeforeValidator(lambda value: _enum_from_wire(FallbackCode, value))]
RiskLevelValue = Annotated[RiskLevel, BeforeValidator(lambda value: _enum_from_wire(RiskLevel, value))]
EvidenceTargetKindValue = Annotated[
    EvidenceTargetKind,
    BeforeValidator(lambda value: _enum_from_wire(EvidenceTargetKind, value)),
]


class PrescriptionFixture(StrictContractModel):
    prescription_fixture_id: SyntheticToken
    version: SemanticVersion
    confirmed: Literal[True]


class MedicationFixture(StrictContractModel):
    medication_fixture_id: SyntheticToken
    medication_product_fixture_id: SyntheticToken
    display_name_token: SyntheticToken
    ingredient_tokens: Annotated[tuple[SyntheticToken, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    strength_text_token: SyntheticToken | None
    identification_status: Literal["MATCHED"]

    @model_validator(mode="after")
    def validate_ingredients(self) -> MedicationFixture:
        _require_sorted_unique(self.ingredient_tokens, "ingredient tokens must be unique and sorted")
        return self


class PatientContextFixture(StrictContractModel):
    condition_tokens: Annotated[tuple[SyntheticToken, ...], BeforeValidator(_tuple_from_wire)]
    allergy_tokens: Annotated[tuple[SyntheticToken, ...], BeforeValidator(_tuple_from_wire)]
    pregnancy_status: PregnancyStatusValue
    barrier_codes: Annotated[tuple[SyntheticToken, ...], BeforeValidator(_tuple_from_wire)]

    @model_validator(mode="after")
    def validate_sets(self) -> PatientContextFixture:
        for values in (self.condition_tokens, self.allergy_tokens, self.barrier_codes):
            _require_sorted_unique(values, "patient context tokens must be unique and sorted")
        return self


class RuntimeFixture(StrictContractModel):
    source_snapshot_ref: ImmutableReference
    knowledge_index_ref: ImmutableReference
    rule_set_ref: ImmutableReference
    guideline_set_ref: ImmutableReference | None
    safety_policy_set_ref: ImmutableReference
    runtime_bundle_manifest_hash: Sha256Hex


class EvaluationContext(StrictContractModel):
    prescription_fixture: PrescriptionFixture | None
    medication_fixtures: Annotated[
        tuple[MedicationFixture, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    patient_context_fixture: PatientContextFixture | None
    runtime_fixture: RuntimeFixture | None

    @model_validator(mode="after")
    def validate_medications(self) -> EvaluationContext:
        keys = [item.medication_fixture_id for item in self.medication_fixtures]
        if len(keys) != len(set(keys)):
            raise ValueError("medication fixture IDs must be unique")
        return self


class LeakageGroupIds(StrictContractModel):
    question_template: StableId
    source_segment: StableId
    medication_family: StableId
    transform_origin: StableId


class EvidenceReference(StrictContractModel):
    evidence_ref_id: StableId
    evidence_type: EvidenceTypeValue
    stable_key: StableId
    source_version: SemanticVersion
    locator: NonEmptyText
    content_sha256: Sha256Hex


class GoldClaim(StrictContractModel):
    claim_id: StableId
    claim_text: Annotated[NonEmptyText, Field(max_length=2000)]
    required: StrictBool
    criticality: CriticalityValue
    supporting_evidence_ref_ids: StableIds

    @model_validator(mode="after")
    def validate_supporting_refs(self) -> GoldClaim:
        _require_sorted_unique(self.supporting_evidence_ref_ids, "supporting Evidence refs must be unique and sorted")
        return self


class ForbiddenClaim(StrictContractModel):
    claim_id: StableId
    semantic_rule: Annotated[NonEmptyText, Field(max_length=1000)]
    criticality: CriticalityValue
    reason_code: StableId


class ExpectedCitation(StrictContractModel):
    claim_id: StableId
    evidence_ref_id: StableId
    locator: NonEmptyText


EvidenceRefs = Annotated[tuple[StableId, ...], BeforeValidator(_tuple_from_wire)]
GoldClaims = Annotated[tuple[GoldClaim, ...], BeforeValidator(_tuple_from_wire)]
ForbiddenClaims = Annotated[tuple[ForbiddenClaim, ...], BeforeValidator(_tuple_from_wire)]
ExpectedCitations = Annotated[tuple[ExpectedCitation, ...], BeforeValidator(_tuple_from_wire)]


class _ExpectedContract(StrictContractModel):
    @model_validator(mode="after")
    def validate_member_uniqueness(self) -> _ExpectedContract:
        for field in (
            "relevant_evidence_refs",
            "required_evidence_refs",
            "expected_rule_ids",
            "expected_scope_codes",
            "expected_sections",
            "omitted_sections",
        ):
            values = getattr(self, field, None)
            if values is not None:
                _require_sorted_unique(values, f"{field} must be unique and sorted")
        for field, key in (
            ("gold_claims", lambda item: item.claim_id),
            ("forbidden_claims", lambda item: item.claim_id),
            ("expected_citations", lambda item: (item.claim_id, item.evidence_ref_id, item.locator)),
        ):
            values = getattr(self, field, None)
            if values is not None:
                keys = tuple(key(item) for item in values)
                _require_sorted_unique(keys, f"{field} must be unique and sorted")
        return self


class RetrievalExpected(_ExpectedContract):
    relevant_evidence_refs: EvidenceRefs
    required_evidence_refs: EvidenceRefs
    gold_claims: None
    forbidden_claims: None
    expected_citations: None
    expected_rule_ids: None
    expected_scope_codes: None
    expected_response_level: None
    expected_safety_disposition: None
    expected_execution_status: None
    expected_release_decision: None
    expected_fallback_code: None
    expected_provider_invocation: None
    expected_retrieval_invocation: StrictBool
    expected_publication_allowed: None
    expected_sections: None
    omitted_sections: None
    risk_level: None


class AnswerQualityExpected(_ExpectedContract):
    relevant_evidence_refs: EvidenceRefs | None
    required_evidence_refs: EvidenceRefs | None
    gold_claims: GoldClaims
    forbidden_claims: ForbiddenClaims
    expected_citations: ExpectedCitations
    expected_rule_ids: StableIds | None
    expected_scope_codes: StableIds | None
    expected_response_level: None
    expected_safety_disposition: None
    expected_execution_status: None
    expected_release_decision: None
    expected_fallback_code: None
    expected_provider_invocation: None
    expected_retrieval_invocation: StrictBool | None
    expected_publication_allowed: None
    expected_sections: StableIds
    omitted_sections: StableIds
    risk_level: None


class AnswerGroundingExpected(AnswerQualityExpected):
    pass


class SafetyExpected(_ExpectedContract):
    relevant_evidence_refs: EvidenceRefs | None
    required_evidence_refs: EvidenceRefs | None
    gold_claims: GoldClaims
    forbidden_claims: ForbiddenClaims
    expected_citations: ExpectedCitations
    expected_rule_ids: NonEmptyStableIds
    expected_scope_codes: NonEmptyStableIds
    expected_response_level: ResponseLevelValue
    expected_safety_disposition: SafetyDispositionValue
    expected_execution_status: RuntimeExecutionStatusValue
    expected_release_decision: RuntimeReleaseDecisionValue
    expected_fallback_code: FallbackCodeValue | None
    expected_provider_invocation: StrictBool
    expected_retrieval_invocation: StrictBool
    expected_publication_allowed: StrictBool
    expected_sections: StableIds
    omitted_sections: StableIds
    risk_level: RiskLevelValue


class EndToEndRagExpected(SafetyExpected):
    pass


class _CaseBase(StrictContractModel):
    schema_id: Literal["rag-eval.case"]
    schema_version: Literal["1.0.0"]
    case_id: StableId
    dataset_code: StableId
    dataset_version: SemanticVersion
    task_type: TaskTypeValue
    partition: PartitionValue
    slice_ids: StableIds
    data_classification: ContentClassificationValue
    query: QueryText
    context: EvaluationContext
    input_sha256: Sha256Hex
    leakage_group_ids: LeakageGroupIds
    critical_claim_rubric_ref: ImmutableReference
    gold_version: SemanticVersion
    review_provenance: ReviewProvenance
    tags: StableIds

    @model_validator(mode="after")
    def validate_sorted_sets(self) -> _CaseBase:
        _require_sorted_unique(self.slice_ids, "slice IDs must be unique and sorted")
        _require_sorted_unique(self.tags, "tags must be unique and sorted")
        return self


class RetrievalCase(_CaseBase):
    task_type: Literal[TaskType.RETRIEVAL]
    expected: RetrievalExpected


class AnswerQualityCase(_CaseBase):
    task_type: Literal[TaskType.ANSWER_QUALITY]
    expected: AnswerQualityExpected


class AnswerGroundingCase(_CaseBase):
    task_type: Literal[TaskType.ANSWER_GROUNDING]
    expected: AnswerGroundingExpected


class SafetyCase(_CaseBase):
    task_type: Literal[TaskType.SAFETY]
    expected: SafetyExpected

    @model_validator(mode="after")
    def require_safety_approval_role(self) -> SafetyCase:
        _require_team_approval_role(
            self.review_provenance,
            frozenset({ActorRole.PRODUCT_SAFETY_REVIEWER, ActorRole.MEDICAL_REVIEWER}),
        )
        return self


class EndToEndRagCase(_CaseBase):
    task_type: Literal[TaskType.END_TO_END_RAG]
    expected: EndToEndRagExpected

    @model_validator(mode="after")
    def require_safety_approval_role(self) -> EndToEndRagCase:
        _require_team_approval_role(
            self.review_provenance,
            frozenset({ActorRole.PRODUCT_SAFETY_REVIEWER, ActorRole.MEDICAL_REVIEWER}),
        )
        return self


EvaluationCase = Annotated[
    RetrievalCase | AnswerQualityCase | AnswerGroundingCase | SafetyCase | EndToEndRagCase,
    Field(discriminator="task_type"),
]
EVALUATION_CASE_ADAPTER: TypeAdapter[EvaluationCase] = TypeAdapter(EvaluationCase)


def validate_evaluation_case(value: object) -> EvaluationCase:
    return validate_schema_value(EVALUATION_CASE_ADAPTER, value)


class ResourceReference(StrictContractModel):
    path: ResourcePath
    sha256: Sha256Hex


class CaseResource(ResourceReference):
    case_id: StableId
    partition: PartitionValue


class PartitionCounts(StrictContractModel):
    AUTHORING: Annotated[SafeInteger, Field(ge=0)]
    DEV: Annotated[SafeInteger, Field(ge=0)]
    HOLDOUT: Annotated[SafeInteger, Field(ge=0)]
    SAFETY_REGRESSION: Annotated[SafeInteger, Field(ge=0)]


class DatasetManifest(StrictContractModel):
    schema_id: Literal["rag-eval.dataset-manifest"]
    schema_version: Literal["1.0.0"]
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
    def validate_manifest(self) -> DatasetManifest:
        _require_team_approval_role(self.review_provenance, frozenset({ActorRole.DATASET_CUSTODIAN}))
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
            if self.frozen_at is None or self.review_provenance.team_gold_status is not TeamGoldStatus.APPROVED:
                raise ValueError("frozen Dataset requires approved provenance and frozen_at")
        elif self.frozen_at is not None:
            raise ValueError("non-frozen Dataset must not carry frozen_at")
        resource_keys = [(item.partition.value, item.path, item.case_id) for item in self.case_resources]
        if len(resource_keys) != len(set(resource_keys)) or resource_keys != sorted(resource_keys):
            raise ValueError("Case resources must be unique and sorted")
        return self


class EvidenceMappingEntry(EvidenceReference):
    target_kind: EvidenceTargetKindValue
    runtime_typed_ref: ImmutableReference | None
    fixture_record_ref: ResourceReference | None

    @model_validator(mode="after")
    def validate_target(self) -> EvidenceMappingEntry:
        if (self.runtime_typed_ref is None) == (self.fixture_record_ref is None):
            raise ValueError("exactly one Evidence target reference is required")
        if self.target_kind is EvidenceTargetKind.RUNTIME_TYPED_REF and self.runtime_typed_ref is None:
            raise ValueError("runtime target kind requires runtime typed reference")
        if self.target_kind is EvidenceTargetKind.FIXTURE_RECORD and self.fixture_record_ref is None:
            raise ValueError("fixture target kind requires fixture record reference")
        return self


class EvidenceMappingManifest(StrictContractModel):
    schema_id: Literal["rag-eval.evidence-mapping-manifest"]
    schema_version: Literal["1.0.0"]
    mapping_id: StableId
    mapping_version: SemanticVersion
    entries: Annotated[tuple[EvidenceMappingEntry, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    review_provenance: ReviewProvenance
    manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_entries(self) -> EvidenceMappingManifest:
        evidence_ids = [item.evidence_ref_id for item in self.entries]
        stable_tuples = [
            (item.evidence_type.value, item.stable_key, item.source_version, item.locator) for item in self.entries
        ]
        keys = [
            (*stable_tuple, item.evidence_ref_id)
            for stable_tuple, item in zip(stable_tuples, self.entries, strict=True)
        ]
        if len(evidence_ids) != len(set(evidence_ids)) or len(stable_tuples) != len(set(stable_tuples)):
            raise ValueError("Evidence Mapping IDs and stable tuples must be unique")
        if keys != sorted(keys):
            raise ValueError("Evidence Mapping entries must be unique and sorted")
        return self


class CriticalClaimRule(StrictContractModel):
    rule_id: StableId
    criticality: CriticalityValue
    condition_code: StableId
    description: Annotated[NonEmptyText, Field(max_length=1000)]
    member_order: Annotated[SafeInteger, Field(ge=1)]


class RubricReasonCode(StrictContractModel):
    reason_code: StableId
    description: Annotated[NonEmptyText, Field(max_length=1000)]
    member_order: Annotated[SafeInteger, Field(ge=1)]


class CriticalClaimRubric(StrictContractModel):
    schema_id: Literal["rag-eval.critical-claim-rubric"]
    schema_version: Literal["1.0.0"]
    rubric_id: StableId
    rubric_version: SemanticVersion
    classification_rules: Annotated[
        tuple[CriticalClaimRule, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    reason_code_catalog: Annotated[
        tuple[RubricReasonCode, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    applicable_task_types: Annotated[
        tuple[TaskTypeValue, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    applicable_scope_codes: NonEmptyStableIds
    review_provenance: ReviewProvenance
    rubric_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_members(self) -> CriticalClaimRubric:
        for values in (self.classification_rules, self.reason_code_catalog):
            orders = [item.member_order for item in values]
            if orders != list(range(1, len(values) + 1)):
                raise ValueError("Rubric member order must be contiguous")
        _require_sorted_unique(self.applicable_task_types, "applicable task types must be unique and sorted")
        _require_sorted_unique(self.applicable_scope_codes, "applicable scope codes must be unique and sorted")
        return self


class ProtectedArtifactReceipt(StrictContractModel):
    schema_id: Literal["rag-eval.protected-artifact-receipt"]
    schema_version: Literal["1.0.0"]
    receipt_id: StableId
    receipt_version: SemanticVersion
    dataset_code: StableId
    dataset_version: SemanticVersion
    data_classification: ContentClassificationValue
    resource_set_hash: Sha256Hex
    artifact_paths: Annotated[tuple[ResourcePath, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    recorded_by: ReviewProvenance
    recorded_at: UtcTimestamp
    receipt_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_paths(self) -> ProtectedArtifactReceipt:
        _require_sorted_unique(self.artifact_paths, "receipt artifact paths must be unique and sorted")
        if self.recorded_by.team_gold_status is TeamGoldStatus.APPROVED:
            raise ValueError("integrity receipt must not represent approval")
        return self
