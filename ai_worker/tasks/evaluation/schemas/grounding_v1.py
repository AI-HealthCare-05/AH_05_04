from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Annotated, Literal, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StrictBool,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.authoring import NonEmptyText
from ai_worker.tasks.evaluation.schemas.common import (
    CanonicalUuid,
    ImmutableReference,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
    TaskType,
)
from ai_worker.tasks.evaluation.schemas.provenance_v1 import RuntimeVersionToken


def _enum_from_wire(enum_type: type[StrEnum], value: object) -> object:
    return enum_type(value) if isinstance(value, str) else value


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be")


def _require_nfc(value: str) -> str:
    if not unicodedata.is_normalized("NFC", value):
        raise ValueError("value must be NFC-normalized")
    return value


def _require_sorted_unique_enum_values(values: tuple[StrEnum, ...], message: str) -> None:
    wire_values = [value.value for value in values]
    if len(wire_values) != len(set(wire_values)) or wire_values != sorted(wire_values, key=_utf16_key):
        raise ValueError(message)


class CitationSourceType(StrEnum):
    PRESCRIPTION = "PRESCRIPTION"
    KNOWLEDGE_CHUNK = "KNOWLEDGE_CHUNK"
    INTERACTION_RULE = "INTERACTION_RULE"
    LIFESTYLE_GUIDELINE = "LIFESTYLE_GUIDELINE"
    SAFETY_POLICY = "SAFETY_POLICY"


class ClaimKind(StrEnum):
    MEDICAL = "MEDICAL"
    AUXILIARY = "AUXILIARY"
    SAFETY_FALLBACK = "SAFETY_FALLBACK"


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class ClaimCriticality(StrEnum):
    CRITICAL = "CRITICAL"
    NON_CRITICAL = "NON_CRITICAL"


class CriticalitySource(StrEnum):
    GOLD_EXACT_MATCH = "GOLD_EXACT_MATCH"
    APPROVED_REVIEW = "APPROVED_REVIEW"


class CandidateValidationExecutionStatus(StrEnum):
    EVALUATED = "EVALUATED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class CandidateValidationDecision(StrEnum):
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class CandidateValidationReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    CLAIM_IDENTITY_INVALID = "CLAIM_IDENTITY_INVALID"
    CITATION_IDENTITY_INVALID = "CITATION_IDENTITY_INVALID"
    EVIDENCE_TYPE_MISMATCH = "EVIDENCE_TYPE_MISMATCH"
    EVIDENCE_PROVENANCE_INVALID = "EVIDENCE_PROVENANCE_INVALID"
    MEDICAL_CLAIM_CITATION_REQUIRED = "MEDICAL_CLAIM_CITATION_REQUIRED"
    MEDICAL_CLAIM_NOT_SUPPORTED = "MEDICAL_CLAIM_NOT_SUPPORTED"
    CLAIM_NOT_SUPPORTED = "CLAIM_NOT_SUPPORTED"
    SUPPORT_RECEIPT_REQUIRED = "SUPPORT_RECEIPT_REQUIRED"
    SUPPORT_RECEIPT_MISMATCH = "SUPPORT_RECEIPT_MISMATCH"


class AuthorizationDecision(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"


class AuthorizationReason(StrEnum):
    VALIDATED_SELECTION_INVALID = "VALIDATED_SELECTION_INVALID"
    RUNTIME_BINDING_INVALID = "RUNTIME_BINDING_INVALID"
    ORIGIN_REQUEST_MISMATCH = "ORIGIN_REQUEST_MISMATCH"
    AUTHORIZATION_SELECTION_INVALID = "AUTHORIZATION_SELECTION_INVALID"
    AUTHORIZATION_SELECTION_REQUIRED = "AUTHORIZATION_SELECTION_REQUIRED"
    AUTHORIZATION_REQUEST_INVALID = "AUTHORIZATION_REQUEST_INVALID"
    RECEIPT_INVALID = "RECEIPT_INVALID"
    RECEIPT_BINDING_MISMATCH = "RECEIPT_BINDING_MISMATCH"
    SELECTION_RECEIPT_MISMATCH = "SELECTION_RECEIPT_MISMATCH"
    SELECTION_NOT_AUTHORIZED = "SELECTION_NOT_AUTHORIZED"


class GroundingSignalStatus(StrEnum):
    EVALUATED = "EVALUATED"
    NOT_APPLICABLE_NO_CLAIMS = "NOT_APPLICABLE_NO_CLAIMS"


CitationSourceTypeValue = Annotated[
    CitationSourceType,
    BeforeValidator(lambda value: _enum_from_wire(CitationSourceType, value)),
]
ClaimKindValue = Annotated[ClaimKind, BeforeValidator(lambda value: _enum_from_wire(ClaimKind, value))]
ClaimSupportStatusValue = Annotated[
    ClaimSupportStatus,
    BeforeValidator(lambda value: _enum_from_wire(ClaimSupportStatus, value)),
]
ClaimCriticalityValue = Annotated[
    ClaimCriticality,
    BeforeValidator(lambda value: _enum_from_wire(ClaimCriticality, value)),
]
CriticalitySourceValue = Annotated[
    CriticalitySource,
    BeforeValidator(lambda value: _enum_from_wire(CriticalitySource, value)),
]
CandidateValidationExecutionStatusValue = Annotated[
    CandidateValidationExecutionStatus,
    BeforeValidator(lambda value: _enum_from_wire(CandidateValidationExecutionStatus, value)),
]
CandidateValidationDecisionValue = Annotated[
    CandidateValidationDecision,
    BeforeValidator(lambda value: _enum_from_wire(CandidateValidationDecision, value)),
]
CandidateValidationReasonValue = Annotated[
    CandidateValidationReason,
    BeforeValidator(lambda value: _enum_from_wire(CandidateValidationReason, value)),
]
AuthorizationDecisionValue = Annotated[
    AuthorizationDecision,
    BeforeValidator(lambda value: _enum_from_wire(AuthorizationDecision, value)),
]
AuthorizationReasonValue = Annotated[
    AuthorizationReason,
    BeforeValidator(lambda value: _enum_from_wire(AuthorizationReason, value)),
]
GroundingSignalStatusValue = Annotated[
    GroundingSignalStatus,
    BeforeValidator(lambda value: _enum_from_wire(GroundingSignalStatus, value)),
]
AnswerGroundingTaskTypeValue = Annotated[
    Literal[TaskType.ANSWER_GROUNDING, TaskType.SAFETY, TaskType.END_TO_END_RAG],
    BeforeValidator(lambda value: _enum_from_wire(TaskType, value)),
]
SafetyTaskTypeValue = Annotated[
    Literal[TaskType.SAFETY, TaskType.END_TO_END_RAG],
    BeforeValidator(lambda value: _enum_from_wire(TaskType, value)),
]
ValidationReasons = Annotated[tuple[CandidateValidationReasonValue, ...], BeforeValidator(_tuple_from_wire)]
AuthorizationReasons = Annotated[tuple[AuthorizationReasonValue, ...], BeforeValidator(_tuple_from_wire)]
SourceVersionToken = Annotated[RuntimeVersionToken, AfterValidator(_require_nfc)]


class CitationEdgeObservation(StrictContractModel):
    citation_key: StableId
    claim_key: StableId
    source_type: CitationSourceTypeValue
    evidence_ref_id: StableId
    source_version: SourceVersionToken
    locator: NonEmptyText
    content_sha256: Sha256Hex
    accepted: StrictBool
    validation_reason_code: CandidateValidationReasonValue | None
    authorized: StrictBool
    authorization_reason_code: AuthorizationReasonValue | None
    authorization_selection_sha256: Sha256Hex | None
    gold_source_matched: StrictBool

    @model_validator(mode="after")
    def validate_decisions(self) -> CitationEdgeObservation:
        if self.accepted != (self.validation_reason_code is None):
            raise ValueError("accepted edge and validation reason must be consistent")
        if self.authorized:
            if self.authorization_reason_code is not None or self.authorization_selection_sha256 is None:
                raise ValueError("authorized edge requires selection hash and no rejection reason")
        elif self.authorization_selection_sha256 is not None:
            raise ValueError("unauthorized edge forbids selection hash")
        return self


class ClaimObservation(StrictContractModel):
    claim_key: StableId
    claim_kind: ClaimKindValue
    criticality: ClaimCriticalityValue
    criticality_source: CriticalitySourceValue
    criticality_review_ref: ImmutableReference | None
    support_status: ClaimSupportStatusValue
    support_receipt_sha256: Sha256Hex
    citations: Annotated[tuple[CitationEdgeObservation, ...], BeforeValidator(_tuple_from_wire)]

    @model_validator(mode="after")
    def validate_claim(self) -> ClaimObservation:
        if (self.criticality_source is CriticalitySource.APPROVED_REVIEW) != (self.criticality_review_ref is not None):
            raise ValueError("approved criticality requires exactly one review reference")
        citation_keys = [citation.citation_key for citation in self.citations]
        if len(citation_keys) != len(set(citation_keys)) or citation_keys != sorted(citation_keys, key=_utf16_key):
            raise ValueError("citation keys must be unique and sorted within each claim")
        if any(citation.claim_key != self.claim_key for citation in self.citations):
            raise ValueError("citation claim key must reference its containing claim")
        return self


class ClaimCitationObservation(StrictContractModel):
    schema_id: Literal["rag-eval.claim-citation-observation"]
    schema_version: Literal["1.0.0"]
    observation_sha256: Sha256Hex
    run_id: CanonicalUuid
    case_id: StableId
    task_type: AnswerGroundingTaskTypeValue
    dataset_code: StableId
    dataset_version: SemanticVersion
    input_sha256: Sha256Hex
    answer_sha256: Sha256Hex
    answer_variant_manifest_hash: Sha256Hex
    validation_execution_status: CandidateValidationExecutionStatusValue
    validation_decision: CandidateValidationDecisionValue
    validation_reason_codes: ValidationReasons
    validated_selection_sha256: Sha256Hex | None
    authorization_decision: AuthorizationDecisionValue | None
    authorization_reason_codes: AuthorizationReasons
    authorization_receipt_ref: ImmutableReference | None
    authorization_receipt_sha256: Sha256Hex | None
    claims: Annotated[tuple[ClaimObservation, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]

    def _validate_collections(self) -> list[str]:
        claim_keys = [claim.claim_key for claim in self.claims]
        if len(claim_keys) != len(set(claim_keys)) or claim_keys != sorted(claim_keys, key=_utf16_key):
            raise ValueError("claim keys must be unique and sorted")
        citation_keys = [citation.citation_key for claim in self.claims for citation in claim.citations]
        if len(citation_keys) != len(set(citation_keys)) or citation_keys != sorted(citation_keys, key=_utf16_key):
            raise ValueError("citation keys must be unique and sorted across the observation")
        _require_sorted_unique_enum_values(self.validation_reason_codes, "validation reasons must be unique and sorted")
        _require_sorted_unique_enum_values(
            self.authorization_reason_codes,
            "authorization reasons must be unique and sorted",
        )
        return citation_keys

    def _validate_validation_outcome(self) -> None:
        if self.validation_decision is CandidateValidationDecision.VALIDATED:
            if (
                self.validation_execution_status is not CandidateValidationExecutionStatus.EVALUATED
                or self.validation_reason_codes
                or self.validated_selection_sha256 is None
            ):
                raise ValueError("validated observation requires evaluated execution and selection hash")
        elif not self.validation_reason_codes or self.validated_selection_sha256 is not None:
            raise ValueError("rejected observation requires reasons and no validated selection hash")

    def _validate_authorized_outcome(
        self,
        receipt_values: tuple[ImmutableReference | None, Sha256Hex | None],
        citations: tuple[CitationEdgeObservation, ...],
    ) -> None:
        if self.authorization_reason_codes or any(value is None for value in receipt_values):
            raise ValueError("authorized observation requires complete receipt binding and no reasons")
        if any(not citation.authorized for citation in citations):
            raise ValueError("authorized observation cannot contain unauthorized edges")

    def _validate_rejected_authorization(
        self,
        receipt_values: tuple[ImmutableReference | None, Sha256Hex | None],
        citations: tuple[CitationEdgeObservation, ...],
    ) -> None:
        if not self.authorization_reason_codes or any(value is not None for value in receipt_values):
            raise ValueError("rejected observation requires reasons and no accepted receipt binding")
        if any(citation.authorized or citation.authorization_reason_code is None for citation in citations):
            raise ValueError("rejected observation requires rejected edge reasons")

    def _validate_authorization_not_run(
        self,
        receipt_values: tuple[ImmutableReference | None, Sha256Hex | None],
        citations: tuple[CitationEdgeObservation, ...],
    ) -> None:
        if self.authorization_reason_codes or any(value is not None for value in receipt_values):
            raise ValueError("missing authorization decision forbids reasons and receipt bindings")
        if any(
            citation.authorized
            or citation.authorization_reason_code is not None
            or citation.authorization_selection_sha256 is not None
            for citation in citations
        ):
            raise ValueError("authorization not run requires empty edge authorization outcomes")

    def _validate_authorization_outcome(self, *, has_citations: bool) -> None:
        receipt_values = (self.authorization_receipt_ref, self.authorization_receipt_sha256)
        citations = tuple(citation for claim in self.claims for citation in claim.citations)
        if self.authorization_decision is AuthorizationDecision.AUTHORIZED:
            self._validate_authorized_outcome(receipt_values, citations)
        elif self.authorization_decision is AuthorizationDecision.REJECTED:
            self._validate_rejected_authorization(receipt_values, citations)
        else:
            self._validate_authorization_not_run(receipt_values, citations)

        validation_succeeded = self.validation_decision is CandidateValidationDecision.VALIDATED
        if validation_succeeded:
            if any(not citation.accepted for citation in citations):
                raise ValueError("validated observation cannot contain rejected validation edges")
            if has_citations != (self.authorization_decision is not None):
                raise ValueError("validated citation presence and authorization decision must be consistent")
        elif self.authorization_decision is not None:
            raise ValueError("rejected validation forbids authorization outcome")

    @model_validator(mode="after")
    def validate_observation(self) -> ClaimCitationObservation:
        citation_keys = self._validate_collections()
        self._validate_validation_outcome()
        self._validate_authorization_outcome(has_citations=bool(citation_keys))
        return self


class GroundingSignal(StrictContractModel):
    schema_id: Literal["rag-eval.grounding-signal"]
    schema_version: Literal["1.0.0"]
    signal_sha256: Sha256Hex
    run_id: CanonicalUuid
    case_id: StableId
    task_type: SafetyTaskTypeValue
    dataset_code: StableId
    dataset_version: SemanticVersion
    input_sha256: Sha256Hex
    answer_sha256: Sha256Hex | None
    status: GroundingSignalStatusValue
    observation_ref: ImmutableReference | None
    observation_sha256: Sha256Hex | None
    critical_unsupported_claim: StrictBool
    uncited_medical_claim: StrictBool
    source_binding_misuse: StrictBool

    @model_validator(mode="after")
    def validate_signal_state(self) -> GroundingSignal:
        evaluated_bindings = (self.answer_sha256, self.observation_ref, self.observation_sha256)
        observation_bindings = (self.observation_ref, self.observation_sha256)
        failures = (
            self.critical_unsupported_claim,
            self.uncited_medical_claim,
            self.source_binding_misuse,
        )
        if self.status is GroundingSignalStatus.EVALUATED:
            if any(value is None for value in evaluated_bindings):
                raise ValueError("evaluated grounding signal requires complete observation binding")
        elif any(value is not None for value in observation_bindings) or any(failures):
            raise ValueError("no-claims grounding signal requires null observation bindings and false failures")
        return self


CLAIM_CITATION_OBSERVATION_ADAPTER: TypeAdapter[ClaimCitationObservation] = TypeAdapter(ClaimCitationObservation)
GROUNDING_SIGNAL_ADAPTER: TypeAdapter[GroundingSignal] = TypeAdapter(GroundingSignal)


def _parse_hashed_projection[T: BaseModel](raw_bytes: bytes, model: type[T], hash_field: str) -> T:
    from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes

    try:
        payload = parse_json_object_bytes(raw_bytes)
        validated = model.model_validate(payload)
    except (EvaluationValidationError, ValidationError):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None
    canonical_payload = cast(dict[str, JsonValue], validated.model_dump(mode="json"))
    if (
        canonical_sha256(canonical_payload, excluded_top_level_keys=frozenset({hash_field}))
        != canonical_payload[hash_field]
    ):
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    try:
        validate_privacy_boundary(canonical_payload)
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN:
            raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_DETECTED, error.safe_path) from None
        raise
    return validated


def parse_claim_citation_observation_bytes(raw_bytes: bytes) -> ClaimCitationObservation:
    return _parse_hashed_projection(raw_bytes, ClaimCitationObservation, "observation_sha256")


def parse_grounding_signal_bytes(raw_bytes: bytes) -> GroundingSignal:
    return _parse_hashed_projection(raw_bytes, GroundingSignal, "signal_sha256")
