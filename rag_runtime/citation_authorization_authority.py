"""Canonical immutable projections for Citation Authorization authority artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

SOURCE_DECISION_ARTIFACT_CODE = "citation_authorization_source_decision"
MEMBER_DECISION_ARTIFACT_CODE = "citation_authorization_member_decision"
RECEIPT_ARTIFACT_CODE = "citation_authorization_receipt"
AUTHORITY_ARTIFACT_VERSION = "1.0"
SOURCE_DECISION_PROJECTION_VERSION = "citation-authorization-source-decision-v1"
MEMBER_DECISION_PROJECTION_VERSION = "citation-authorization-member-decision-v1"
RECEIPT_PROJECTION_VERSION = "citation-authorization-receipt-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CitationAuthorityDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class CitationAuthorityReason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_REVOKED = "APPROVAL_REVOKED"
    SOURCE_INACTIVE = "SOURCE_INACTIVE"
    SNAPSHOT_NOT_CURRENT = "SNAPSHOT_NOT_CURRENT"
    SOURCE_DECISION_FAILED = "SOURCE_DECISION_FAILED"
    MEMBER_BINDING_INVALID = "MEMBER_BINDING_INVALID"
    ENDPOINT_NOT_VERIFIED = "ENDPOINT_NOT_VERIFIED"
    ENDPOINT_DISABLED = "ENDPOINT_DISABLED"
    ENDPOINT_NOT_APPROVED = "ENDPOINT_NOT_APPROVED"
    OPERATION_DISABLED = "OPERATION_DISABLED"
    OPERATION_NOT_APPROVED = "OPERATION_NOT_APPROVED"
    ARTIFACT_BINDING_INVALID = "ARTIFACT_BINDING_INVALID"


@dataclass(frozen=True, slots=True)
class CitationAuthorityArtifactRef:
    artifact_code: str
    version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class CitationSourceDecisionProjection:
    request_sha256: str
    request_guard_decision_id: UUID
    origin_guard_artifact_code: str
    origin_guard_artifact_version: str
    origin_guard_content_sha256: str
    user_id: UUID
    request_operation_code: str
    environment: str
    bundle_id: UUID
    bundle_manifest_hash: str
    scope_manifest_hash: str
    source_snapshot_id: UUID
    source_use_approval_id: UUID
    source_code: str
    source_version: str
    approval_version: str
    purpose: str
    evaluation_time: datetime
    approval_valid_from: datetime
    approval_expires_at: datetime
    approval_revoked_at: datetime | None
    source_lifecycle_status: str
    snapshot_verification_status: str
    actual_decision_outcome: CitationAuthorityDecision
    reason_code: CitationAuthorityReason


@dataclass(frozen=True, slots=True)
class CitationMemberDecisionProjection:
    request_sha256: str
    source_decision_content_sha256: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    member_kind: str
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None
    evaluation_time: datetime
    endpoint_lifecycle_status: str | None
    endpoint_runtime_status: str | None
    endpoint_acquisition_status: str | None
    operation_runtime_status: str | None
    operation_acquisition_status: str | None
    current_member_kind: str | None
    snapshot_endpoint_id: UUID | None
    snapshot_operation_id: UUID | None
    current_endpoint_id: UUID | None
    current_operation_id: UUID | None
    current_ingestion_artifact_id: UUID | None
    artifact_fk_exists: bool | None
    actual_decision_outcome: CitationAuthorityDecision
    reason_code: CitationAuthorityReason


@dataclass(frozen=True, slots=True)
class CitationReceiptSelectionProjection:
    selection_order: int
    source_code: str
    source_version: str
    member_kind: str
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None
    source_decision_content_sha256: str
    member_decision_content_sha256: str
    selected_for_operation: bool
    purpose: str
    source_decision: str
    member_decision: str


@dataclass(frozen=True, slots=True)
class CitationReceiptProjection:
    request_sha256: str
    origin_guard_artifact_code: str
    origin_guard_artifact_version: str
    origin_guard_content_sha256: str
    origin_decision: str
    operation: str
    environment: str
    bundle_id: str
    bundle_manifest_hash: str
    request_scope_codes: tuple[str, ...]
    scope_manifest_hash: str
    validated_selection_sha256: str
    selection_manifest_sha256: str
    selections: tuple[CitationReceiptSelectionProjection, ...]


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    return value


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    encoded = json.dumps(_canonical_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return unicodedata.normalize("NFC", encoded).encode("utf-8")


def _require_sha256(value: object, field: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256")


def _require_text(value: object, field: str) -> None:
    if type(value) is not str or not value or value != value.strip() or not unicodedata.is_normalized("NFC", value):
        raise ValueError(f"{field} must be canonical nonblank text")


def _validate_source(value: CitationSourceDecisionProjection) -> None:
    if type(value) is not CitationSourceDecisionProjection:
        raise ValueError("source decision projection type is invalid")
    for field in ("request_sha256", "origin_guard_content_sha256", "bundle_manifest_hash", "scope_manifest_hash"):
        _require_sha256(getattr(value, field), field)
    for field in (
        "origin_guard_artifact_code",
        "origin_guard_artifact_version",
        "request_operation_code",
        "environment",
        "source_code",
        "source_version",
        "approval_version",
        "purpose",
        "source_lifecycle_status",
        "snapshot_verification_status",
    ):
        _require_text(getattr(value, field), field)
    if value.purpose != "PATIENT_CITATION":
        raise ValueError("purpose must be PATIENT_CITATION")
    if type(value.actual_decision_outcome) is not CitationAuthorityDecision:
        raise ValueError("actual_decision_outcome is invalid")
    if type(value.reason_code) is not CitationAuthorityReason:
        raise ValueError("reason_code is invalid")
    if (
        value.actual_decision_outcome is CitationAuthorityDecision.PASS
        and value.reason_code is not CitationAuthorityReason.ELIGIBLE
    ):
        raise ValueError("PASS source decision must be ELIGIBLE")
    for instant in (value.evaluation_time, value.approval_valid_from, value.approval_expires_at):
        _canonical_value(instant)
    if value.approval_revoked_at is not None:
        _canonical_value(value.approval_revoked_at)


def _validate_member(value: CitationMemberDecisionProjection) -> None:
    if type(value) is not CitationMemberDecisionProjection:
        raise ValueError("member decision projection type is invalid")
    _require_sha256(value.request_sha256, "request_sha256")
    _require_sha256(value.source_decision_content_sha256, "source_decision_content_sha256")
    for field in ("source_code", "source_version", "member_kind"):
        _require_text(getattr(value, field), field)
    if value.member_kind == "ENDPOINT_OPERATION":
        _require_text(value.endpoint_code, "endpoint_code")
        if value.artifact_code is not None or value.artifact_version is not None:
            raise ValueError("endpoint member cannot have artifact identity")
    elif value.member_kind == "ARTIFACT_MEMBER":
        _require_text(value.artifact_code, "artifact_code")
        _require_text(value.artifact_version, "artifact_version")
        if value.endpoint_code is not None or value.operation_code is not None:
            raise ValueError("artifact member cannot have endpoint identity")
    else:
        raise ValueError("member_kind is invalid")
    _canonical_value(value.evaluation_time)
    if type(value.actual_decision_outcome) is not CitationAuthorityDecision:
        raise ValueError("actual_decision_outcome is invalid")
    if type(value.reason_code) is not CitationAuthorityReason:
        raise ValueError("reason_code is invalid")
    if (
        value.actual_decision_outcome is CitationAuthorityDecision.PASS
        and value.reason_code is not CitationAuthorityReason.ELIGIBLE
    ):
        raise ValueError("PASS member decision must be ELIGIBLE")


def _validate_receipt(value: CitationReceiptProjection) -> None:
    if type(value) is not CitationReceiptProjection:
        raise ValueError("receipt projection type is invalid")
    for field in (
        "request_sha256",
        "origin_guard_content_sha256",
        "bundle_manifest_hash",
        "scope_manifest_hash",
        "validated_selection_sha256",
        "selection_manifest_sha256",
    ):
        _require_sha256(getattr(value, field), field)
    if not value.selections:
        raise ValueError("receipt selections are required")
    if tuple(item.selection_order for item in value.selections) != tuple(range(len(value.selections))):
        raise ValueError("receipt selection order must be contiguous")


AuthorityProjection = CitationSourceDecisionProjection | CitationMemberDecisionProjection | CitationReceiptProjection


def _compute_ref(*, code: str, projection_version: str, value: AuthorityProjection) -> CitationAuthorityArtifactRef:
    payload = asdict(value)
    payload["projection_version"] = projection_version
    return CitationAuthorityArtifactRef(
        artifact_code=code,
        version=AUTHORITY_ARTIFACT_VERSION,
        content_sha256=hashlib.sha256(_canonical_bytes(payload)).hexdigest(),
    )


def compute_citation_source_decision_ref(
    value: CitationSourceDecisionProjection,
) -> CitationAuthorityArtifactRef:
    _validate_source(value)
    return _compute_ref(
        code=SOURCE_DECISION_ARTIFACT_CODE, projection_version=SOURCE_DECISION_PROJECTION_VERSION, value=value
    )


def compute_citation_member_decision_ref(
    value: CitationMemberDecisionProjection,
) -> CitationAuthorityArtifactRef:
    _validate_member(value)
    return _compute_ref(
        code=MEMBER_DECISION_ARTIFACT_CODE, projection_version=MEMBER_DECISION_PROJECTION_VERSION, value=value
    )


def compute_citation_receipt_ref(value: CitationReceiptProjection) -> CitationAuthorityArtifactRef:
    _validate_receipt(value)
    return _compute_ref(code=RECEIPT_ARTIFACT_CODE, projection_version=RECEIPT_PROJECTION_VERSION, value=value)


__all__ = [
    "AUTHORITY_ARTIFACT_VERSION",
    "MEMBER_DECISION_ARTIFACT_CODE",
    "MEMBER_DECISION_PROJECTION_VERSION",
    "RECEIPT_ARTIFACT_CODE",
    "RECEIPT_PROJECTION_VERSION",
    "SOURCE_DECISION_ARTIFACT_CODE",
    "SOURCE_DECISION_PROJECTION_VERSION",
    "CitationAuthorityArtifactRef",
    "CitationAuthorityDecision",
    "CitationAuthorityReason",
    "CitationMemberDecisionProjection",
    "CitationReceiptProjection",
    "CitationReceiptSelectionProjection",
    "CitationSourceDecisionProjection",
    "compute_citation_member_decision_ref",
    "compute_citation_receipt_ref",
    "compute_citation_source_decision_ref",
]
