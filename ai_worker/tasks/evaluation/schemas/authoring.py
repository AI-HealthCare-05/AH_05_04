from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, StringConstraints, TypeAdapter, model_validator

from ai_worker.tasks.evaluation.schemas.common import (
    ActorRole,
    ContentClassification,
    NonEmptyString,
    Partition,
    ResourcePath,
    ReviewProvenance,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
    TaskType,
    TeamGoldStatus,
    validate_schema_value,
)

_GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _enum_from_wire(enum_type: type[StrEnum], value: object) -> object:
    if isinstance(value, str):
        return enum_type(value)
    return value


def _validate_git_sha(value: str) -> str:
    if _GIT_SHA_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a lowercase Git commit SHA")
    return value


def _require_team_approval_role(
    provenance: ReviewProvenance,
    allowed_roles: frozenset[ActorRole],
) -> None:
    if provenance.team_gold_status is not TeamGoldStatus.APPROVED:
        return
    if provenance.approved_by is None or provenance.approved_by.role not in allowed_roles:
        raise ValueError("team gold approval actor role is not permitted for this schema")


GitCommitSha = Annotated[
    str,
    StringConstraints(strict=True, pattern=_GIT_SHA_PATTERN.pattern),
    AfterValidator(_validate_git_sha),
]
PartitionValue = Annotated[Partition, BeforeValidator(lambda value: _enum_from_wire(Partition, value))]
ContentClassificationValue = Annotated[
    ContentClassification,
    BeforeValidator(lambda value: _enum_from_wire(ContentClassification, value)),
]
TaskTypeValue = Annotated[TaskType, BeforeValidator(lambda value: _enum_from_wire(TaskType, value))]
EvidenceIdList = Annotated[list[StableId], Field(min_length=1)]


class EvidenceType(StrEnum):
    PRESCRIPTION = "PRESCRIPTION"
    KNOWLEDGE_CHUNK = "KNOWLEDGE_CHUNK"
    INTERACTION_RULE = "INTERACTION_RULE"
    LIFESTYLE_GUIDELINE = "LIFESTYLE_GUIDELINE"
    SAFETY_POLICY = "SAFETY_POLICY"


EvidenceTypeValue = Annotated[
    EvidenceType,
    BeforeValidator(lambda value: _enum_from_wire(EvidenceType, value)),
]


class EvaluationContext(StrictContractModel):
    prescription_fixture: ResourcePath | None
    medication_fixtures: list[ResourcePath]
    patient_context_fixture: ResourcePath | None
    runtime_fixture: ResourcePath | None


class RetrievalExpected(StrictContractModel):
    gold_evidence_ids: EvidenceIdList
    gold_claims: None
    gold_citation_evidence_ids: None
    gold_rule_ids: None
    expected_scope: None
    expected_safety_disposition: None


class AnswerQualityExpected(StrictContractModel):
    gold_evidence_ids: None
    gold_claims: EvidenceIdList
    gold_citation_evidence_ids: None
    gold_rule_ids: None
    expected_scope: StableId | None
    expected_safety_disposition: None


class AnswerGroundingExpected(StrictContractModel):
    gold_evidence_ids: None
    gold_claims: EvidenceIdList
    gold_citation_evidence_ids: EvidenceIdList
    gold_rule_ids: None
    expected_scope: StableId | None
    expected_safety_disposition: None


class SafetyExpected(StrictContractModel):
    gold_evidence_ids: None
    gold_claims: None
    gold_citation_evidence_ids: None
    gold_rule_ids: EvidenceIdList
    expected_scope: StableId
    expected_safety_disposition: StableId


class EndToEndRagExpected(StrictContractModel):
    gold_evidence_ids: EvidenceIdList
    gold_claims: EvidenceIdList
    gold_citation_evidence_ids: EvidenceIdList
    gold_rule_ids: list[StableId]
    expected_scope: StableId
    expected_safety_disposition: StableId


class _CaseBase(StrictContractModel):
    schema_version: Literal["1.0.0"]
    case_id: StableId
    dataset_code: StableId
    dataset_version: SemanticVersion
    partition: PartitionValue
    content_classification: ContentClassificationValue
    input_hash: Sha256Hex
    question: NonEmptyString
    context: EvaluationContext
    leakage_groups: dict[
        Literal["question_template", "source_segment", "medication_family", "transform_origin"],
        StableId,
    ]
    review_provenance: ReviewProvenance

    @model_validator(mode="after")
    def require_all_leakage_axes(self) -> _CaseBase:
        if set(self.leakage_groups) != {
            "question_template",
            "source_segment",
            "medication_family",
            "transform_origin",
        }:
            raise ValueError("all leakage axes are required")
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
    task_type: TaskTypeValue


class DatasetManifest(StrictContractModel):
    schema_version: Literal["1.0.0"]
    dataset_code: StableId
    dataset_version: SemanticVersion
    content_classification: ContentClassificationValue
    fixture_git_commit_sha: GitCommitSha | None
    protected_artifact_receipt_ref: StableId | None
    deidentification_approval_receipt_ref: StableId | None
    case_resources: Annotated[list[CaseResource], Field(min_length=1)]
    evidence_mapping: ResourceReference
    critical_claim_rubric: ResourceReference
    review_provenance: ReviewProvenance
    content_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_source_and_privacy_provenance(self) -> DatasetManifest:
        _require_team_approval_role(
            self.review_provenance,
            frozenset({ActorRole.DATASET_CUSTODIAN}),
        )
        if (self.fixture_git_commit_sha is None) == (self.protected_artifact_receipt_ref is None):
            raise ValueError("exactly one dataset source provenance is required")
        if (
            self.content_classification is ContentClassification.APPROVED_DEIDENTIFIED
            and self.deidentification_approval_receipt_ref is None
        ):
            raise ValueError("approved deidentified data requires an approval receipt")
        if (
            self.content_classification is ContentClassification.SYNTHETIC
            and self.deidentification_approval_receipt_ref is not None
        ):
            raise ValueError("synthetic data must not claim deidentification approval")
        return self


class EvidenceReference(StrictContractModel):
    evidence_id: StableId
    evidence_type: EvidenceTypeValue
    resource_path: ResourcePath
    resource_hash: Sha256Hex
    locator: NonEmptyString


class EvidenceMappingManifest(StrictContractModel):
    schema_version: Literal["1.0.0"]
    dataset_code: StableId
    dataset_version: SemanticVersion
    evidence: Annotated[list[EvidenceReference], Field(min_length=1)]
    review_provenance: ReviewProvenance
    content_hash: Sha256Hex

    @model_validator(mode="after")
    def require_unique_evidence_ids(self) -> EvidenceMappingManifest:
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique")
        return self


class CriticalClaimRubric(StrictContractModel):
    schema_version: Literal["1.0.0"]
    dataset_code: StableId
    dataset_version: SemanticVersion
    critical_claim_keys: Annotated[list[StableId], Field(min_length=1)]
    review_provenance: ReviewProvenance
    content_hash: Sha256Hex

    @model_validator(mode="after")
    def require_unique_claim_keys(self) -> CriticalClaimRubric:
        if len(self.critical_claim_keys) != len(set(self.critical_claim_keys)):
            raise ValueError("critical claim keys must be unique")
        return self
