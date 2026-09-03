from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from ai_worker.tasks.evaluation.schemas.authoring import (
    CriticalClaimRubric,
    DatasetStatus,
    EvidenceMappingManifest,
    ProtectedArtifactReceipt,
)
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import (
    AnswerGroundingExpectedV11,
    AnswerQualityExpectedV11,
    DatasetManifestV11,
    EndToEndRagExpectedV11,
    RetrievalExpectedV11,
    SafetyExpectedV11,
    _CaseBaseV11,
    _validate_dependency_fault,
    _validate_not_invoked,
    _validate_rule_inputs,
)
from ai_worker.tasks.evaluation.schemas.common import TaskType, TeamGoldStatus
from ai_worker.tasks.evaluation.schemas.common_v1_2 import (
    ActorRoleV12,
    ReviewProvenanceV12,
)


def _require_team_approval_role_v12(
    provenance: ReviewProvenanceV12,
    allowed_roles: frozenset[ActorRoleV12],
) -> None:
    if provenance.team_gold_status is not TeamGoldStatus.APPROVED:
        return
    if provenance.approved_by is None or provenance.approved_by.role not in allowed_roles:
        raise ValueError("team gold approval actor role is not permitted for this schema")


class _CaseBaseV12(_CaseBaseV11):
    schema_version: Literal["1.2.0"]
    review_provenance: ReviewProvenanceV12


class RetrievalCaseV12(_CaseBaseV12):
    task_type: Literal[TaskType.RETRIEVAL]
    expected: RetrievalExpectedV11


class AnswerQualityCaseV12(_CaseBaseV12):
    task_type: Literal[TaskType.ANSWER_QUALITY]
    expected: AnswerQualityExpectedV11


class AnswerGroundingCaseV12(_CaseBaseV12):
    task_type: Literal[TaskType.ANSWER_GROUNDING]
    expected: AnswerGroundingExpectedV11


class _RuleOutcomeCaseV12(_CaseBaseV12):
    expected: SafetyExpectedV11

    @model_validator(mode="after")
    def validate_rule_outcome_context(self) -> _RuleOutcomeCaseV12:
        expected = self.expected
        runtime = self.context.runtime_fixture
        if runtime is None:
            raise ValueError("Safety Rule outcome requires a runtime fixture")
        if expected.expected_rule_outcome.value == "NOT_INVOKED":
            _validate_not_invoked(expected, runtime)
        else:
            _validate_rule_inputs(runtime)
        _validate_dependency_fault(expected, runtime)
        return self


class SafetyCaseV12(_RuleOutcomeCaseV12):
    task_type: Literal[TaskType.SAFETY]
    expected: SafetyExpectedV11

    @model_validator(mode="after")
    def require_safety_approval_role(self) -> SafetyCaseV12:
        _require_team_approval_role_v12(
            self.review_provenance,
            frozenset({ActorRoleV12.PRODUCT_SAFETY_REVIEWER, ActorRoleV12.MEDICAL_REVIEWER}),
        )
        return self


class EndToEndRagCaseV12(_RuleOutcomeCaseV12):
    task_type: Literal[TaskType.END_TO_END_RAG]
    expected: EndToEndRagExpectedV11

    @model_validator(mode="after")
    def require_safety_approval_role(self) -> EndToEndRagCaseV12:
        _require_team_approval_role_v12(
            self.review_provenance,
            frozenset({ActorRoleV12.PRODUCT_SAFETY_REVIEWER, ActorRoleV12.MEDICAL_REVIEWER}),
        )
        return self


EvaluationCaseV12 = Annotated[
    RetrievalCaseV12 | AnswerQualityCaseV12 | AnswerGroundingCaseV12 | SafetyCaseV12 | EndToEndRagCaseV12,
    Field(discriminator="task_type"),
]
EVALUATION_CASE_ADAPTER_V1_2: TypeAdapter[EvaluationCaseV12] = TypeAdapter(EvaluationCaseV12)


class DatasetManifestV12(DatasetManifestV11):
    schema_version: Literal["1.2.0"]
    review_provenance: ReviewProvenanceV12

    @model_validator(mode="after")
    def validate_manifest(self) -> DatasetManifestV12:
        provenance = self.review_provenance
        _require_team_approval_role_v12(provenance, frozenset({ActorRoleV12.DATASET_CUSTODIAN}))
        if (self.fixture_git_commit_sha is None) == (self.protected_artifact_receipt_ref is None):
            raise ValueError("exactly one dataset source provenance is required")
        if self.data_classification.value == "APPROVED_DEIDENTIFIED" and self.deidentification_approval_receipt_ref is None:
            raise ValueError("approved deidentified data requires an approval receipt")
        if self.data_classification.value == "SYNTHETIC" and self.deidentification_approval_receipt_ref is not None:
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


class EvidenceMappingManifestV12(EvidenceMappingManifest):
    schema_version: Literal["1.2.0"]
    review_provenance: ReviewProvenanceV12


class CriticalClaimRubricV12(CriticalClaimRubric):
    schema_version: Literal["1.2.0"]
    review_provenance: ReviewProvenanceV12


class ProtectedArtifactReceiptV12(ProtectedArtifactReceipt):
    schema_version: Literal["1.2.0"]
    recorded_by: ReviewProvenanceV12
