from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from ai_worker.tasks.evaluation.canonical import normalize_resource_path
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError

SCHEMA_VERSION = "1.0.0"
MIN_SAFE_INTEGER = -(2**53) + 1
MAX_SAFE_INTEGER = (2**53) - 1

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$")
_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
_SEMANTIC_VERSION_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")


class Partition(StrEnum):
    AUTHORING = "AUTHORING"
    DEV = "DEV"
    HOLDOUT = "HOLDOUT"
    SAFETY_REGRESSION = "SAFETY_REGRESSION"


class ExperimentType(StrEnum):
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL"
    ANSWER_GROUNDING_SAFETY = "ANSWER_GROUNDING_SAFETY"
    END_TO_END_RAG = "END_TO_END_RAG"


class TaskType(StrEnum):
    RETRIEVAL = "RETRIEVAL"
    ANSWER_QUALITY = "ANSWER_QUALITY"
    ANSWER_GROUNDING = "ANSWER_GROUNDING"
    SAFETY = "SAFETY"
    END_TO_END_RAG = "END_TO_END_RAG"


class ExecutionStatus(StrEnum):
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NOT_EVALUATED = "NOT_EVALUATED"
    INVALID = "INVALID"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"


class DecisionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "N/A"


class ContentClassification(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    APPROVED_DEIDENTIFIED = "APPROVED_DEIDENTIFIED"


class LeakageAxis(StrEnum):
    QUESTION_TEMPLATE = "question_template"
    SOURCE_SEGMENT = "source_segment"
    MEDICATION_FAMILY = "medication_family"
    TRANSFORM_ORIGIN = "transform_origin"


def _validate_sha256(value: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a lowercase SHA-256 hex value")
    return value


def _validate_uuid(value: str) -> str:
    if _UUID_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("must be a canonical UUID") from error
    if str(parsed) != value:
        raise ValueError("must be a canonical UUID")
    return value


def _validate_decimal(value: str) -> str:
    if _DECIMAL_PATTERN.fullmatch(value) is None or value == "-0":
        raise ValueError("must be a canonical decimal string")
    return value


def _validate_timestamp(value: str) -> str:
    if _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a canonical UTC timestamp")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError("must be a valid UTC timestamp") from error
    return value


def _validate_semantic_version(value: str) -> str:
    if _SEMANTIC_VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a canonical semantic version")
    return value


def _enum_from_wire(enum_type: type[StrEnum], value: Any) -> Any:
    if isinstance(value, str):
        return enum_type(value)
    return value


SafeInteger = Annotated[StrictInt, Field(ge=MIN_SAFE_INTEGER, le=MAX_SAFE_INTEGER)]
Sha256Hex = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_sha256)]
CanonicalUuid = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_uuid)]
CanonicalDecimal = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_decimal)]
UtcTimestamp = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_timestamp)]
ResourcePath = Annotated[str, StringConstraints(strict=True), AfterValidator(normalize_resource_path)]
SemanticVersion = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_semantic_version)]
NonEmptyString = Annotated[str, StringConstraints(strict=True, min_length=1)]
ExecutionStatusValue = Annotated[
    ExecutionStatus,
    BeforeValidator(lambda value: _enum_from_wire(ExecutionStatus, value)),
]
DecisionStatusValue = Annotated[
    DecisionStatus,
    BeforeValidator(lambda value: _enum_from_wire(DecisionStatus, value)),
]


def ensure_unique_resource_paths(paths: list[str]) -> list[str]:
    if len(paths) != len(set(paths)):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_DUPLICATE)
    return paths


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ActorRef(StrictContractModel):
    namespace: NonEmptyString
    actor_id: NonEmptyString
    display_name: NonEmptyString | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return (self.namespace, self.actor_id)


class ImmutableReference(StrictContractModel):
    resource_id: NonEmptyString
    resource_version: SemanticVersion
    resource_hash: Sha256Hex


class ReviewProvenance(StrictContractModel):
    proposed_by: ActorRef
    approved_by: ActorRef
    reviewed_at: UtcTimestamp

    @model_validator(mode="after")
    def reject_self_approval(self) -> ReviewProvenance:
        if self.proposed_by.identity == self.approved_by.identity:
            raise ValueError("proposer and approver must be different actors")
        return self


class ExecutionDecisionMixin(StrictContractModel):
    execution_status: ExecutionStatusValue
    decision_status: DecisionStatusValue | None

    @model_validator(mode="after")
    def validate_execution_decision(self) -> ExecutionDecisionMixin:
        if self.execution_status is ExecutionStatus.COMPLETED:
            if self.decision_status is None:
                raise ValueError("completed execution requires a decision")
        elif self.decision_status is not None:
            raise ValueError("incomplete execution must not have a decision")
        if getattr(self, "required", False) and self.decision_status is DecisionStatus.NOT_APPLICABLE:
            raise ValueError("required execution cannot be not applicable")
        return self
