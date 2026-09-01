from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, StrictBool, model_validator

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.schemas.common import (
    ActorRef,
    CanonicalDecimal,
    ExperimentType,
    ImmutableReference,
    LeakageAxis,
    NonEmptyString,
    Partition,
    ReviewProvenance,
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


ExperimentTypeValue = Annotated[
    ExperimentType,
    BeforeValidator(lambda value: _enum_from_wire(ExperimentType, value)),
]
PartitionValue = Annotated[Partition, BeforeValidator(lambda value: _enum_from_wire(Partition, value))]
TaskTypeValue = Annotated[TaskType, BeforeValidator(lambda value: _enum_from_wire(TaskType, value))]
LeakageAxisValue = Annotated[
    LeakageAxis,
    BeforeValidator(lambda value: _enum_from_wire(LeakageAxis, value)),
]
PositiveSafeInteger = Annotated[SafeInteger, Field(ge=1)]


class EvaluationProfile(StrictContractModel):
    schema_version: Literal["1.0.0"]
    profile_code: NonEmptyString
    profile_version: SemanticVersion
    runtime_eligible: StrictBool
    required_experiment_types: Annotated[list[ExperimentTypeValue], Field(min_length=1)]
    required_partitions: Annotated[list[PartitionValue], Field(min_length=1)]
    suite_references: Annotated[list[ImmutableReference], Field(min_length=1)]
    review_provenance: ReviewProvenance
    content_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_release_scope(self) -> EvaluationProfile:
        if self.runtime_eligible:
            if ExperimentType.END_TO_END_RAG not in self.required_experiment_types:
                raise ValueError("runtime-eligible profiles require END_TO_END_RAG")
            required_partitions = {Partition.HOLDOUT, Partition.SAFETY_REGRESSION}
            if not required_partitions.issubset(self.required_partitions):
                raise ValueError("runtime-eligible profiles require HOLDOUT and SAFETY_REGRESSION")
        return self


class SuiteDefinition(StrictContractModel):
    schema_version: Literal["1.0.0"]
    suite_code: NonEmptyString
    suite_version: SemanticVersion
    experiment_type: ExperimentTypeValue
    partitions: Annotated[list[PartitionValue], Field(min_length=1)]
    task_types: Annotated[list[TaskTypeValue], Field(min_length=1)]
    required: StrictBool
    review_provenance: ReviewProvenance
    content_hash: Sha256Hex


CiParameterValue = CanonicalDecimal | SafeInteger | NonEmptyString | StrictBool | None


class ComparisonScope(StrictContractModel):
    metric_code: NonEmptyString
    metric_version: SemanticVersion
    partition: PartitionValue
    slice_key: NonEmptyString
    required: StrictBool
    analysis_unit: NonEmptyString
    estimator: NonEmptyString
    minimum_case_count: PositiveSafeInteger
    minimum_independent_group_count: PositiveSafeInteger
    cluster_dimension: LeakageAxisValue
    threshold: CanonicalDecimal
    decision_basis: NonEmptyString
    ci_method: NonEmptyString
    ci_method_version: SemanticVersion
    ci_parameters: dict[NonEmptyString, CiParameterValue]
    seed: SafeInteger | None


class ComparisonPolicy(StrictContractModel):
    schema_version: Literal["1.0.0"]
    policy_code: NonEmptyString
    policy_version: SemanticVersion
    scopes: Annotated[list[ComparisonScope], Field(min_length=1)]
    proposed_by: ActorRef
    approved_by: ActorRef
    reviewed_at: UtcTimestamp
    content_hash: Sha256Hex

    @model_validator(mode="after")
    def reject_self_approval(self) -> ComparisonPolicy:
        if self.proposed_by.identity == self.approved_by.identity:
            raise ValueError("proposer and approver must be different actors")
        return self


class PolicyMemberType(StrEnum):
    PROFILE = "PROFILE"
    SUITE = "SUITE"
    COMPARISON_POLICY = "COMPARISON_POLICY"


PolicyMemberTypeValue = Annotated[
    PolicyMemberType,
    BeforeValidator(lambda value: _enum_from_wire(PolicyMemberType, value)),
]


class EvaluationPolicyMember(StrictContractModel):
    member_order: PositiveSafeInteger
    member_type: PolicyMemberTypeValue
    reference: ImmutableReference

    @property
    def natural_key(self) -> tuple[PolicyMemberType, str, str]:
        return (self.member_type, self.reference.resource_id, self.reference.resource_version)


def evaluation_policy_member_manifest_hash(members: list[EvaluationPolicyMember]) -> str:
    ordered_members = sorted(members, key=lambda member: member.member_order)
    member_values: list[JsonValue] = [
        {
            "member_order": member.member_order,
            "member_type": member.member_type.value,
            "reference": {
                "resource_id": member.reference.resource_id,
                "resource_version": member.reference.resource_version,
                "resource_hash": member.reference.resource_hash,
            },
        }
        for member in ordered_members
    ]
    envelope: JsonValue = {"members": member_values}
    return canonical_sha256(envelope)


class EvaluationPolicy(StrictContractModel):
    schema_version: Literal["1.0.0"]
    policy_code: NonEmptyString
    policy_version: SemanticVersion
    members: Annotated[list[EvaluationPolicyMember], Field(min_length=1)]
    member_manifest_hash: Sha256Hex
    review_provenance: ReviewProvenance
    content_hash: Sha256Hex

    @model_validator(mode="after")
    def validate_members(self) -> EvaluationPolicy:
        natural_keys = [member.natural_key for member in self.members]
        if len(natural_keys) != len(set(natural_keys)):
            raise ValueError("evaluation policy member natural keys must be unique")

        member_orders = [member.member_order for member in self.members]
        if len(member_orders) != len(set(member_orders)):
            raise ValueError("evaluation policy member orders must be unique")

        expected_hash = evaluation_policy_member_manifest_hash(self.members)
        if self.member_manifest_hash != expected_hash:
            raise ValueError("evaluation policy member manifest hash does not match")
        return self
