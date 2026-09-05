from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, BeforeValidator, Field, StrictInt, StringConstraints, ValidationError, model_validator

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.common import (
    ImmutableReference,
    LeakageAxis,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
    UtcTimestamp,
)
from ai_worker.tasks.evaluation.schemas.common_v1_2 import ReviewProvenanceV12


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _leakage_axis_from_wire(value: object) -> object:
    return LeakageAxis(value) if isinstance(value, str) else value


PositiveInteger = Annotated[StrictInt, Field(gt=0)]
NonEmptyText = Annotated[str, StringConstraints(strict=True, min_length=1)]


class AuthoringIdentityEntry(StrictContractModel):
    member_order: PositiveInteger
    case_id: StableId
    question_template_id: StableId
    source_segment_id: StableId
    medication_family_id: StableId
    transform_origin_id: StableId
    question_template_spec: NonEmptyText
    source_snapshot_ref: ImmutableReference
    source_locator: NonEmptyText
    source_chunk_sha256: Sha256Hex
    medication_family_fixture_id: StableId
    base_intent_seed: StableId
    transform_spec: NonEmptyText


class AuthoringIdentityManifest(StrictContractModel):
    schema_id: Literal["rag-eval.authoring-identity-manifest"]
    schema_version: Literal["1.0.0"]
    manifest_id: StableId
    manifest_version: SemanticVersion
    dataset_code: StableId
    dataset_version: SemanticVersion
    canonicalization_spec_version: SemanticVersion
    entries: Annotated[
        tuple[AuthoringIdentityEntry, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    manifest_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_entries(self) -> AuthoringIdentityManifest:
        orders = [entry.member_order for entry in self.entries]
        case_ids = [entry.case_id for entry in self.entries]
        if len(orders) != len(set(orders)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("authoring identity member orders and Case IDs must be unique")
        if orders != sorted(orders):
            raise ValueError("authoring identity entries must be sorted by member order")
        return self


class IndexBridgeEntry(StrictContractModel):
    evidence_ref_id: StableId
    evidence_mapping_stable_key: StableId
    evidence_key: StableId
    knowledge_chunk_ref: StableId
    source_locator: NonEmptyText
    source_version: SemanticVersion
    content_sha256: Sha256Hex


class IndexBuildReceipt(StrictContractModel):
    schema_id: Literal["rag-eval.index-build-receipt"]
    schema_version: Literal["1.0.0"]
    receipt_id: StableId
    receipt_version: SemanticVersion
    dataset_ref: ImmutableReference
    evidence_mapping_ref: ImmutableReference
    source_snapshot_ref: ImmutableReference
    evidence_index_ref: ImmutableReference
    build_config_ref: ImmutableReference
    adapter_artifact_ref: ImmutableReference
    canonicalization_spec_version: SemanticVersion
    bridge_entries: Annotated[
        tuple[IndexBridgeEntry, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    built_at: UtcTimestamp
    built_by: ReviewProvenanceV12
    receipt_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_bridge_entries(self) -> IndexBuildReceipt:
        for field in ("evidence_ref_id", "evidence_key", "knowledge_chunk_ref"):
            values = [getattr(entry, field) for entry in self.bridge_entries]
            if len(values) != len(set(values)):
                raise ValueError(f"index bridge {field} values must be unique")
        keys = [(entry.evidence_ref_id, entry.evidence_key, entry.knowledge_chunk_ref) for entry in self.bridge_entries]
        if keys != sorted(keys):
            raise ValueError("index bridge entries must be sorted")
        return self


class StudySplitAxisSummary(StrictContractModel):
    axis: Annotated[LeakageAxis, BeforeValidator(_leakage_axis_from_wire)]
    comparison_count: PositiveInteger
    intersection_count: Literal[0]


class StudySplitReceipt(StrictContractModel):
    schema_id: Literal["rag-eval.study-split-receipt"]
    schema_version: Literal["1.0.0"]
    receipt_id: StableId
    receipt_version: SemanticVersion
    dev_dataset_ref: ImmutableReference
    holdout_dataset_ref: ImmutableReference
    dev_authoring_identity_manifest_ref: ImmutableReference
    holdout_authoring_identity_manifest_ref: ImmutableReference
    evidence_index_ref: ImmutableReference
    evaluation_config_ref: ImmutableReference
    gold_schema_ref: ImmutableReference
    canonical_identity_hmac_algorithm_ref: ImmutableReference
    hmac_key_version: StableId
    query_fingerprint_algorithm_ref: ImmutableReference
    simple_substitution_fingerprint_algorithm_ref: ImmutableReference
    transform_fingerprint_algorithm_ref: ImmutableReference
    axis_summaries: Annotated[
        tuple[StudySplitAxisSummary, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=4, max_length=4),
    ]
    authorization_receipt_ref: ImmutableReference
    recorded_at: UtcTimestamp
    recorded_by: ReviewProvenanceV12
    receipt_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_split(self) -> StudySplitReceipt:
        if self.dev_dataset_ref == self.holdout_dataset_ref:
            raise ValueError("DEV and HOLDOUT Dataset refs must be distinct")
        if self.dev_authoring_identity_manifest_ref == self.holdout_authoring_identity_manifest_ref:
            raise ValueError("DEV and HOLDOUT authoring identity manifest refs must be distinct")
        expected_axes = (
            LeakageAxis.QUESTION_TEMPLATE,
            LeakageAxis.SOURCE_SEGMENT,
            LeakageAxis.MEDICATION_FAMILY,
            LeakageAxis.TRANSFORM_ORIGIN,
        )
        if tuple(summary.axis for summary in self.axis_summaries) != expected_axes:
            raise ValueError("study split axis summaries must contain the four leakage axes in order")
        return self


def _parse_hashed_model[T: BaseModel](
    raw_bytes: bytes,
    model: type[T],
    hash_field: str,
) -> T:
    from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes

    try:
        payload = parse_json_object_bytes(raw_bytes)
        validated = model.model_validate(payload)
    except (EvaluationValidationError, ValidationError):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None
    canonical_payload = cast(dict[str, JsonValue], validated.model_dump(mode="json"))
    expected = cast(str, canonical_payload[hash_field])
    if (
        canonical_sha256(
            canonical_payload,
            excluded_top_level_keys=frozenset({hash_field}),
        )
        != expected
    ):
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    try:
        validate_privacy_boundary(canonical_payload)
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN:
            raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_DETECTED, error.safe_path) from None
        raise
    return validated


def parse_authoring_identity_manifest_bytes(raw_bytes: bytes) -> AuthoringIdentityManifest:
    return _parse_hashed_model(raw_bytes, AuthoringIdentityManifest, "manifest_sha256")


def parse_index_build_receipt_bytes(raw_bytes: bytes) -> IndexBuildReceipt:
    return _parse_hashed_model(raw_bytes, IndexBuildReceipt, "receipt_sha256")


def parse_study_split_receipt_bytes(raw_bytes: bytes) -> StudySplitReceipt:
    return _parse_hashed_model(raw_bytes, StudySplitReceipt, "receipt_sha256")
