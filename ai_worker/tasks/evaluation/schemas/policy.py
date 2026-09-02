from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, PlainSerializer, StrictBool, WithJsonSchema, model_validator

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
    StableId,
    StrictContractModel,
    TaskType,
    UtcTimestamp,
)


def _enum_from_wire(enum_type: type[StrEnum], value: object) -> object:
    return enum_type(value) if isinstance(value, str) else value


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _require_sorted_unique(values: tuple[object, ...], message: str) -> None:
    if len(values) != len(set(values)) or list(values) != sorted(values, key=repr):
        raise ValueError(message)


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
ExperimentTypes = Annotated[
    tuple[ExperimentTypeValue, ...],
    BeforeValidator(_tuple_from_wire),
    Field(min_length=1),
]
Partitions = Annotated[
    tuple[PartitionValue, ...],
    BeforeValidator(_tuple_from_wire),
    Field(min_length=1),
]
TaskTypes = Annotated[
    tuple[TaskTypeValue, ...],
    BeforeValidator(_tuple_from_wire),
    Field(min_length=1),
]
References = Annotated[tuple[ImmutableReference, ...], BeforeValidator(_tuple_from_wire)]
StableIds = Annotated[tuple[StableId, ...], BeforeValidator(_tuple_from_wire)]


class TriggerCatalogEntry(StrictContractModel):
    trigger_id: StableId
    member_order: PositiveSafeInteger


class EvaluationProfile(StrictContractModel):
    schema_id: Literal["rag-eval.evaluation-profile"]
    schema_version: Literal["1.0.0"]
    evaluation_profile_id: StableId
    evaluation_profile_version: SemanticVersion
    evaluation_profile_hash: Sha256Hex
    required_experiment_types: ExperimentTypes
    required_partitions: Partitions
    required_gate_refs: References
    required_suite_refs: References
    trigger_catalog: Annotated[tuple[TriggerCatalogEntry, ...], BeforeValidator(_tuple_from_wire)]
    runtime_eligible: StrictBool
    review_provenance: ReviewProvenance

    @model_validator(mode="after")
    def validate_profile(self) -> EvaluationProfile:
        for values, message in (
            (self.required_experiment_types, "experiment types must be unique and sorted"),
            (self.required_partitions, "partitions must be unique and sorted"),
            (self.required_gate_refs, "gate references must be unique and sorted"),
            (self.required_suite_refs, "suite references must be unique and sorted"),
        ):
            keys = tuple(
                (item.id, item.version, item.hash) if isinstance(item, ImmutableReference) else item for item in values
            )
            _require_sorted_unique(keys, message)
        trigger_orders = [item.member_order for item in self.trigger_catalog]
        trigger_ids = [item.trigger_id for item in self.trigger_catalog]
        if trigger_orders != list(range(1, len(trigger_orders) + 1)) or len(trigger_ids) != len(set(trigger_ids)):
            raise ValueError("trigger catalog must have unique IDs and contiguous order")
        if self.runtime_eligible:
            if ExperimentType.END_TO_END_RAG not in self.required_experiment_types:
                raise ValueError("runtime-eligible profiles require END_TO_END_RAG")
            if not {Partition.HOLDOUT, Partition.SAFETY_REGRESSION}.issubset(self.required_partitions):
                raise ValueError("runtime-eligible profiles require HOLDOUT and SAFETY_REGRESSION")
        return self


class SuiteInputSelector(StrictContractModel):
    dataset_code: StableId
    dataset_version: SemanticVersion
    partitions: Partitions
    task_types: TaskTypes


class SuiteDefinition(StrictContractModel):
    schema_id: Literal["rag-eval.suite-definition"]
    schema_version: Literal["1.0.0"]
    suite_id: StableId
    suite_version: SemanticVersion
    suite_hash: Sha256Hex
    adapter_id: StableId
    command: Annotated[tuple[NonEmptyString, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    input_selector: SuiteInputSelector
    expected_case_set_hash: Sha256Hex
    critical_invariant_ids: StableIds
    pass_rule: StableId
    artifact_contract_version: SemanticVersion
    required: StrictBool
    review_provenance: ReviewProvenance

    @model_validator(mode="after")
    def validate_sets(self) -> SuiteDefinition:
        _require_sorted_unique(self.critical_invariant_ids, "critical invariant IDs must be unique and sorted")
        _require_sorted_unique(self.input_selector.partitions, "Suite partitions must be unique and sorted")
        _require_sorted_unique(self.input_selector.task_types, "Suite task types must be unique and sorted")
        return self


CiParameterScalar = CanonicalDecimal | SafeInteger | NonEmptyString | StrictBool | None
CiParameterEntries = tuple[tuple[NonEmptyString, CiParameterScalar], ...]


def _ci_parameters_from_wire(value: object) -> object:
    if type(value) is not dict:
        raise ValueError("CI parameters must be a JSON object")
    if any(type(key) is not str or not key for key in value):
        raise ValueError("CI parameter keys must be non-empty strict strings")
    return tuple(sorted(value.items()))


def _serialize_ci_parameters(parameters: CiParameterEntries) -> dict[str, CiParameterScalar]:
    return dict(parameters)


_CI_PARAMETERS_JSON_SCHEMA = {
    "type": "object",
    "propertyNames": {"type": "string", "minLength": 1},
    "additionalProperties": {
        "anyOf": [
            {"type": "string", "minLength": 1},
            {"type": "integer", "minimum": -(2**53) + 1, "maximum": (2**53) - 1},
            {"type": "boolean"},
            {"type": "null"},
        ]
    },
}
ConfidenceIntervalParameters = Annotated[
    CiParameterEntries,
    BeforeValidator(_ci_parameters_from_wire),
    PlainSerializer(_serialize_ci_parameters, return_type=dict[str, CiParameterScalar]),
    WithJsonSchema(_CI_PARAMETERS_JSON_SCHEMA, mode="validation"),
    WithJsonSchema(_CI_PARAMETERS_JSON_SCHEMA, mode="serialization"),
]


class ComparisonScope(StrictContractModel):
    metric_id: StableId
    metric_version: SemanticVersion
    partition: PartitionValue
    slice_id: StableId
    required: StrictBool
    unit_of_analysis: StableId
    estimator_id: StableId
    estimator_version: SemanticVersion
    minimum_case_count: PositiveSafeInteger
    independence_unit: StableId | None
    cluster_dimension: LeakageAxisValue | None
    minimum_independent_group_count: PositiveSafeInteger | None
    threshold: CanonicalDecimal
    decision_basis: StableId
    ci_method_id: StableId
    ci_method_version: SemanticVersion
    ci_parameters: ConfidenceIntervalParameters
    seed: SafeInteger | None

    @model_validator(mode="after")
    def validate_independence(self) -> ComparisonScope:
        clustered = self.cluster_dimension is not None
        if clustered != (self.minimum_independent_group_count is not None):
            raise ValueError("cluster dimension and minimum independent group count must be paired")
        return self


class ComparisonPolicy(StrictContractModel):
    schema_id: Literal["rag-eval.comparison-policy"]
    schema_version: Literal["1.0.0"]
    comparison_policy_id: StableId
    comparison_policy_version: SemanticVersion
    comparison_policy_hash: Sha256Hex
    scopes: Annotated[tuple[ComparisonScope, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    controlled_variable_keys: Annotated[
        tuple[StableId, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    proposed_by: ActorRef
    approved_by: ActorRef
    approved_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_policy(self) -> ComparisonPolicy:
        if self.proposed_by.identity == self.approved_by.identity:
            raise ValueError("proposer and approver must be different actors")
        _require_sorted_unique(self.controlled_variable_keys, "controlled variable keys must be unique and sorted")
        scope_keys = [(item.metric_id, item.metric_version, item.partition, item.slice_id) for item in self.scopes]
        if len(scope_keys) != len(set(scope_keys)):
            raise ValueError("Comparison Scope natural keys must be unique")
        return self


class PolicyMemberType(StrEnum):
    PROFILE = "PROFILE"
    COMPARISON_POLICY = "COMPARISON_POLICY"
    PARTITION = "PARTITION"
    GATE = "GATE"
    SUITE = "SUITE"
    ARTIFACT_SCHEMA_SET = "ARTIFACT_SCHEMA_SET"


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
        return (self.member_type, self.reference.id, self.reference.version)


def evaluation_policy_member_manifest_hash(members: Sequence[EvaluationPolicyMember]) -> str:
    values: list[JsonValue] = [
        member.model_dump(mode="json") for member in sorted(members, key=lambda item: item.member_order)
    ]
    return canonical_sha256({"members": values})


class EvaluationPolicy(StrictContractModel):
    schema_id: Literal["rag-eval.evaluation-policy"]
    schema_version: Literal["1.0.0"]
    evaluation_policy_id: StableId
    evaluation_policy_version: SemanticVersion
    evaluation_policy_hash: Sha256Hex
    evaluation_profile_ref: EvaluationPolicyMember
    comparison_policy_ref: EvaluationPolicyMember
    required_partition_refs: Annotated[tuple[EvaluationPolicyMember, ...], BeforeValidator(_tuple_from_wire)]
    required_gate_refs: Annotated[tuple[EvaluationPolicyMember, ...], BeforeValidator(_tuple_from_wire)]
    required_suite_refs: Annotated[tuple[EvaluationPolicyMember, ...], BeforeValidator(_tuple_from_wire)]
    artifact_schema_set_ref: EvaluationPolicyMember
    member_manifest_hash: Sha256Hex
    review_provenance: ReviewProvenance

    @property
    def members(self) -> tuple[EvaluationPolicyMember, ...]:
        return (
            self.evaluation_profile_ref,
            self.comparison_policy_ref,
            *self.required_partition_refs,
            *self.required_gate_refs,
            *self.required_suite_refs,
            self.artifact_schema_set_ref,
        )

    @model_validator(mode="after")
    def validate_members(self) -> EvaluationPolicy:
        explicit_types = (
            (self.evaluation_profile_ref.member_type, PolicyMemberType.PROFILE),
            (self.comparison_policy_ref.member_type, PolicyMemberType.COMPARISON_POLICY),
            (self.artifact_schema_set_ref.member_type, PolicyMemberType.ARTIFACT_SCHEMA_SET),
        )
        if any(actual is not expected for actual, expected in explicit_types):
            raise ValueError("explicit Evaluation Policy reference has wrong member type")
        if any(item.member_type is not PolicyMemberType.PARTITION for item in self.required_partition_refs):
            raise ValueError("required partition refs must be PARTITION members")
        if any(item.member_type is not PolicyMemberType.GATE for item in self.required_gate_refs):
            raise ValueError("required gate refs must be GATE members")
        if any(item.member_type is not PolicyMemberType.SUITE for item in self.required_suite_refs):
            raise ValueError("required Suite refs must be SUITE members")
        members = self.members
        keys = [member.natural_key for member in members]
        orders = [member.member_order for member in members]
        if len(keys) != len(set(keys)) or orders != list(range(1, len(members) + 1)):
            raise ValueError("Evaluation Policy members require unique keys and contiguous order")
        if self.member_manifest_hash != evaluation_policy_member_manifest_hash(members):
            raise ValueError("Evaluation Policy member manifest hash does not match")
        return self
