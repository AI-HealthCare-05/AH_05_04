from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from enum import StrEnum
from json import dumps
from typing import Annotated, Any, overload
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from ai_worker.tasks.evaluation.canonical import JsonValue, normalize_resource_path
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary

SCHEMA_VERSION = "1.0.0"
MIN_SAFE_INTEGER = -(2**53) + 1
MAX_SAFE_INTEGER = (2**53) - 1

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*|-[1-9][0-9]*|(?:0|[1-9][0-9]*|-(?:0|[1-9][0-9]*))\.[0-9]*[1-9])$")
_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
_SEMANTIC_VERSION_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_STABLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_RESOURCE_SEGMENT_SCHEMA_PATTERN = r"(?:[^./\\\x00][^/\\\x00]*|\.[^./\\\x00][^/\\\x00]*|\.\.[^/\\\x00]+)"
_RESOURCE_FIRST_SEGMENT_SCHEMA_PATTERN = (
    r"(?:[A-Za-z](?:[^:/\\\x00][^/\\\x00]*)?|[^A-Za-z./\\\x00][^/\\\x00]*|"
    r"\.[^./\\\x00][^/\\\x00]*|\.\.[^/\\\x00]+)"
)
_RESOURCE_PATH_SCHEMA_PATTERN = rf"^{_RESOURCE_FIRST_SEGMENT_SCHEMA_PATTERN}(?:/{_RESOURCE_SEGMENT_SCHEMA_PATTERN})*$"
_SAFE_LOCATION_SEGMENT = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


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


class ActorNamespace(StrEnum):
    GITHUB_LOGIN = "GITHUB_LOGIN"
    EXTERNAL_APPROVAL_REGISTRY = "EXTERNAL_APPROVAL_REGISTRY"
    SYSTEM = "SYSTEM"


class ActorRole(StrEnum):
    EVALUATION_IMPLEMENTER = "EVALUATION_IMPLEMENTER"
    DATASET_CUSTODIAN = "DATASET_CUSTODIAN"
    PRODUCT_SAFETY_REVIEWER = "PRODUCT_SAFETY_REVIEWER"
    MEDICAL_REVIEWER = "MEDICAL_REVIEWER"
    PRIVACY_REVIEWER = "PRIVACY_REVIEWER"
    SYSTEM_VALIDATOR = "SYSTEM_VALIDATOR"


class TeamGoldStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"


class ExternalMedicalReviewStatus(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


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


def _validate_stable_id(value: str) -> str:
    if _STABLE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a stable identifier")
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
Sha256Hex = Annotated[
    str,
    StringConstraints(strict=True, pattern=_SHA256_PATTERN.pattern),
    AfterValidator(_validate_sha256),
]
CanonicalUuid = Annotated[
    str,
    StringConstraints(strict=True, pattern=_UUID_PATTERN.pattern),
    Field(json_schema_extra={"format": "uuid"}),
    AfterValidator(_validate_uuid),
]
CanonicalDecimal = Annotated[
    str,
    StringConstraints(strict=True, pattern=_DECIMAL_PATTERN.pattern),
    AfterValidator(_validate_decimal),
]
UtcTimestamp = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$",
    ),
    Field(json_schema_extra={"format": "date-time"}),
    AfterValidator(_validate_timestamp),
]
ResourcePath = Annotated[
    str,
    StringConstraints(strict=True, pattern=_RESOURCE_PATH_SCHEMA_PATTERN),
    AfterValidator(normalize_resource_path),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(strict=True, pattern=_SEMANTIC_VERSION_PATTERN.pattern),
    AfterValidator(_validate_semantic_version),
]
StableId = Annotated[
    str,
    StringConstraints(strict=True, pattern=_STABLE_ID_PATTERN.pattern),
    AfterValidator(_validate_stable_id),
]
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
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class ActorRef(StrictContractModel):
    namespace: Annotated[
        ActorNamespace,
        BeforeValidator(lambda value: _enum_from_wire(ActorNamespace, value)),
    ]
    actor_id: StableId
    role: Annotated[
        ActorRole,
        BeforeValidator(lambda value: _enum_from_wire(ActorRole, value)),
    ]

    @property
    def identity(self) -> tuple[str, str]:
        return (self.namespace, self.actor_id)


class ImmutableReference(StrictContractModel):
    id: StableId
    version: SemanticVersion
    hash: Sha256Hex


def _immutable_reference_sort_key(reference: ImmutableReference) -> tuple[str, str, str]:
    return (reference.id, reference.version, reference.hash)


class ReviewProvenance(StrictContractModel):
    authored_by: ActorRef
    reviewed_by: ActorRef
    approved_by: ActorRef | None
    authored_at: UtcTimestamp
    reviewed_at: UtcTimestamp
    approved_at: UtcTimestamp | None
    team_gold_status: Annotated[
        TeamGoldStatus,
        BeforeValidator(lambda value: _enum_from_wire(TeamGoldStatus, value)),
    ]
    external_medical_review_status: Annotated[
        ExternalMedicalReviewStatus,
        BeforeValidator(lambda value: _enum_from_wire(ExternalMedicalReviewStatus, value)),
    ]
    external_medical_approval_receipt_ref: ImmutableReference | None
    evidence_review_refs: Annotated[
        tuple[ImmutableReference, ...],
        BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
    ]

    @model_validator(mode="after")
    def validate_provenance(self) -> ReviewProvenance:
        self._validate_actor_rules()
        self._validate_team_gold_rules()
        self._validate_external_review_rules()
        self._validate_evidence_refs()
        return self

    def _validate_actor_rules(self) -> None:
        actors = [self.authored_by, self.reviewed_by]
        if self.approved_by is not None:
            actors.append(self.approved_by)
        identities = [actor.identity for actor in actors]
        if len(identities) != len(set(identities)):
            raise ValueError("author, reviewer, and approver must be different actors")
        if any(
            actor.namespace is ActorNamespace.SYSTEM or actor.role is ActorRole.SYSTEM_VALIDATOR for actor in actors
        ):
            raise ValueError("human review provenance cannot use system actors")
        if self.approved_by is not None and self.approved_by.role is ActorRole.EVALUATION_IMPLEMENTER:
            raise ValueError("an evaluation implementer cannot approve review provenance")

    def _validate_team_gold_rules(self) -> None:
        if self.authored_at > self.reviewed_at:
            raise ValueError("authored_at must not be after reviewed_at")
        if self.team_gold_status is TeamGoldStatus.APPROVED:
            if self.approved_by is None or self.approved_at is None:
                raise ValueError("approved provenance requires an approver and approval time")
            if self.reviewed_at > self.approved_at:
                raise ValueError("reviewed_at must not be after approved_at")
        elif self.approved_by is not None or self.approved_at is not None:
            raise ValueError("non-approved provenance must not carry approval fields")

    def _validate_external_review_rules(self) -> None:
        if self.external_medical_review_status is ExternalMedicalReviewStatus.APPROVED:
            if self.external_medical_approval_receipt_ref is None:
                raise ValueError("external medical approval requires an immutable receipt")
        elif self.external_medical_approval_receipt_ref is not None:
            raise ValueError("non-approved external review must not carry an approval receipt")

    def _validate_evidence_refs(self) -> None:
        keys = [_immutable_reference_sort_key(reference) for reference in self.evidence_review_refs]
        if len(keys) != len(set(keys)) or keys != sorted(keys):
            raise ValueError("evidence review references must be unique and sorted")


def _safe_text(value: str) -> str:
    try:
        validate_privacy_boundary({"detail": value})
    except EvaluationValidationError:
        return "REDACTED"
    if len(value) > 500 or any(ord(character) < 0x20 for character in value):
        return "REDACTED"
    return value


def _safe_location_segment(value: object) -> str | int:
    if isinstance(value, int):
        return value
    text = str(value)
    if _SAFE_LOCATION_SEGMENT.fullmatch(text) is None:
        return "*"
    return _safe_text(text)


def _safe_context_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list | tuple):
        return [_safe_context_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _safe_text(str(key)): _safe_context_value(item)
            for key, item in value.items()
            if _safe_text(str(key)) != "REDACTED"
        }
    return _safe_text(str(value))


class SchemaValidationError(ValueError):
    """Public validation error containing only sanitized, JSON-safe details."""

    def __init__(self, error: ValidationError) -> None:
        details: list[dict[str, JsonValue]] = []
        for item in error.errors(include_input=False):
            detail: dict[str, JsonValue] = {
                "type": _safe_text(str(item.get("type", "value_error"))),
                "loc": [_safe_location_segment(segment) for segment in item.get("loc", ())],
                "msg": _safe_text(str(item.get("msg", "Value is invalid"))),
            }
            context = item.get("ctx")
            if isinstance(context, dict):
                safe_context = _safe_context_value(context)
                if isinstance(safe_context, dict) and safe_context:
                    detail["ctx"] = safe_context
            details.append(detail)
        self._details = details
        super().__init__(EvaluationErrorCode.SCHEMA_INVALID.value)

    def errors(self) -> list[dict[str, JsonValue]]:
        return deepcopy(self._details)

    def json(self) -> str:
        return dumps(self._details, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@overload
def validate_schema_value[ModelValue: BaseModel](validator: type[ModelValue], value: object) -> ModelValue: ...


@overload
def validate_schema_value[AdapterValue](validator: TypeAdapter[AdapterValue], value: object) -> AdapterValue: ...


def validate_schema_value(
    validator: type[BaseModel] | TypeAdapter[Any],
    value: object,
) -> BaseModel | Any:
    try:
        if isinstance(validator, TypeAdapter):
            validated = validator.validate_python(value)
        else:
            validated = validator.model_validate(value)
    except ValidationError as error:
        raise SchemaValidationError(error) from None
    if isinstance(validated, BaseModel):
        validate_privacy_boundary(validated.model_dump(mode="json"))
    return validated


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
