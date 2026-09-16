"""Shared Pure Contract Kernel for RAG Source Member Identity and Authority Binding.

Contract Version:
- SOURCE_MEMBER_IDENTITY_CONTRACT_VERSION = "source-member-identity-v1"
- ENDPOINT_MEMBER_CONTRACT_VERSION = "endpoint-member-contract-v1"

Authority Boundary & Limits:
1. Pure Kernel Scope:
   This module provides pure, side-effect-free, in-memory identity modeling and
   validation for Source Snapshot Members (Endpoint/Operation and Ingestion Artifact).
   It performs NO I/O, database access, network calls, or logging.

2. Verification vs. Authorization:
   `verify_member_authority_binding` checks only structural validity and 5-field
   exact equality between observed and selected identities. It does NOT verify Decision
   ownership, actual `PASS` outcome from the #174 REQUEST Guard, or issuer authenticity.
   The designation 'Authorized' is strictly forbidden at this boundary; only 'Verified'
   or exact structural match is asserted (per PR #623 term invariance rules).

3. Persistence Value Mapping (F7 resolution):
   In the database schema (`rag_source_snapshot_member.member_kind`), ORM models,
   and ingestion adapters, artifact members are represented by the string "ARTIFACT".
   In the pure kernel, `SourceMemberKind.ARTIFACT_MEMBER` is used.
   `persisted_member_kind_value` and `member_kind_from_persisted` bridge this gap deterministically.

4. Caller Responsibility for Exceptions:
   `member_kind_from_persisted` raises `SourceMemberIdentityError` containing the typed
   rejection reason `SourceMemberIdentityReason.PERSISTED_MEMBER_KIND_UNKNOWN`.
   Downstream #180 orchestration must catch this exception and convert it to its own typed
   rejection reason; callers must NOT allow this exception to escape unhandled.
   Alternatively, callers may use `try_member_kind_from_persisted` to receive a tuple without exceptions.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

SOURCE_MEMBER_IDENTITY_CONTRACT_VERSION = "source-member-identity-v1"
ENDPOINT_MEMBER_CONTRACT_VERSION = "endpoint-member-contract-v1"


class SourceMemberKind(StrEnum):
    ENDPOINT_OPERATION = "ENDPOINT_OPERATION"
    ARTIFACT_MEMBER = "ARTIFACT_MEMBER"


class EndpointMemberOperationCodePolicy(StrEnum):
    NULLABLE = "NULLABLE"


ENDPOINT_MEMBER_OPERATION_CODE_POLICY = EndpointMemberOperationCodePolicy.NULLABLE


class SourceMemberIdentityReason(StrEnum):
    MEMBER_KIND_INVALID = "MEMBER_KIND_INVALID"
    ENDPOINT_CODE_REQUIRED = "ENDPOINT_CODE_REQUIRED"
    OPERATION_CODE_INVALID = "OPERATION_CODE_INVALID"
    ARTIFACT_FIELDS_FORBIDDEN = "ARTIFACT_FIELDS_FORBIDDEN"
    ARTIFACT_CODE_REQUIRED = "ARTIFACT_CODE_REQUIRED"
    ARTIFACT_VERSION_REQUIRED = "ARTIFACT_VERSION_REQUIRED"
    ENDPOINT_FIELDS_FORBIDDEN = "ENDPOINT_FIELDS_FORBIDDEN"
    PERSISTED_MEMBER_KIND_UNKNOWN = "PERSISTED_MEMBER_KIND_UNKNOWN"
    MEMBER_AUTHORITY_MISMATCH = "MEMBER_AUTHORITY_MISMATCH"


@dataclass(frozen=True, slots=True)
class SourceMemberIdentity:
    member_kind: SourceMemberKind
    endpoint_code: str | None = None
    operation_code: str | None = None
    artifact_code: str | None = None
    artifact_version: str | None = None


class SourceMemberIdentityError(Exception):
    """Raised when a persisted member kind string cannot be mapped to a known SourceMemberKind.

    Downstream orchestration must convert this exception to a typed rejection reason.
    """

    def __init__(self, reason: SourceMemberIdentityReason) -> None:
        self.reason = reason
        super().__init__(str(reason))


SourceMemberIdentityValidationError = SourceMemberIdentityError


_PERSISTED_TO_KIND: dict[str, SourceMemberKind] = {
    "ENDPOINT_OPERATION": SourceMemberKind.ENDPOINT_OPERATION,
    "ARTIFACT": SourceMemberKind.ARTIFACT_MEMBER,
}

_KIND_TO_PERSISTED: dict[SourceMemberKind, str] = {
    SourceMemberKind.ENDPOINT_OPERATION: "ENDPOINT_OPERATION",
    SourceMemberKind.ARTIFACT_MEMBER: "ARTIFACT",
}


def persisted_member_kind_value(kind: SourceMemberKind) -> str:
    """Map a kernel SourceMemberKind enum to the persisted database wire value."""
    if type(kind) is not SourceMemberKind:
        raise SourceMemberIdentityError(SourceMemberIdentityReason.MEMBER_KIND_INVALID)
    persisted = _KIND_TO_PERSISTED.get(kind)
    if persisted is None:
        raise SourceMemberIdentityError(SourceMemberIdentityReason.MEMBER_KIND_INVALID)
    return persisted


def member_kind_from_persisted(value: str) -> SourceMemberKind:
    """Map a persisted database wire value string to a kernel SourceMemberKind enum.

    Raises SourceMemberIdentityError(PERSISTED_MEMBER_KIND_UNKNOWN) on unknown value.
    """
    kind = _PERSISTED_TO_KIND.get(value)
    if kind is None:
        raise SourceMemberIdentityError(SourceMemberIdentityReason.PERSISTED_MEMBER_KIND_UNKNOWN)
    return kind


def try_member_kind_from_persisted(
    value: str,
) -> tuple[SourceMemberKind | None, SourceMemberIdentityReason | None]:
    """Map a persisted value without raising an exception."""
    kind = _PERSISTED_TO_KIND.get(value)
    if kind is None:
        return None, SourceMemberIdentityReason.PERSISTED_MEMBER_KIND_UNKNOWN
    return kind, None


def _is_nonblank_nfc(val: object) -> bool:
    return type(val) is str and len(val) > 0 and val == val.strip() and unicodedata.is_normalized("NFC", val)


def _validate_endpoint_operation(value: SourceMemberIdentity) -> set[SourceMemberIdentityReason]:
    reasons: set[SourceMemberIdentityReason] = set()
    if not _is_nonblank_nfc(value.endpoint_code):
        reasons.add(SourceMemberIdentityReason.ENDPOINT_CODE_REQUIRED)
    if value.operation_code is not None and not _is_nonblank_nfc(value.operation_code):
        reasons.add(SourceMemberIdentityReason.OPERATION_CODE_INVALID)
    if value.artifact_code is not None or value.artifact_version is not None:
        reasons.add(SourceMemberIdentityReason.ARTIFACT_FIELDS_FORBIDDEN)
    return reasons


def _validate_artifact_member(value: SourceMemberIdentity) -> set[SourceMemberIdentityReason]:
    reasons: set[SourceMemberIdentityReason] = set()
    if not _is_nonblank_nfc(value.artifact_code):
        reasons.add(SourceMemberIdentityReason.ARTIFACT_CODE_REQUIRED)
    if not _is_nonblank_nfc(value.artifact_version):
        reasons.add(SourceMemberIdentityReason.ARTIFACT_VERSION_REQUIRED)
    if value.endpoint_code is not None or value.operation_code is not None:
        reasons.add(SourceMemberIdentityReason.ENDPOINT_FIELDS_FORBIDDEN)
    return reasons


def validate_source_member_identity(value: object) -> tuple[SourceMemberIdentityReason, ...]:
    """Validate a SourceMemberIdentity instance fail-closed, collecting all reasons deterministically.

    Rules:
    - value must be of exact type SourceMemberIdentity.
    - member_kind must be of exact type SourceMemberKind (strings and foreign StrEnums are rejected).
      If not, returns (MEMBER_KIND_INVALID,) only.
    - ENDPOINT_OPERATION:
        - endpoint_code is required (non-blank NFC string).
        - operation_code is nullable; if not None, must be a non-blank NFC string.
        - artifact_code and artifact_version must both be None.
    - ARTIFACT_MEMBER:
        - artifact_code and artifact_version are required (non-blank NFC strings).
        - endpoint_code and operation_code must both be None.
    - All reasons are collected and returned as a sorted tuple without duplicates.
    """
    if type(value) is not SourceMemberIdentity:
        return (SourceMemberIdentityReason.MEMBER_KIND_INVALID,)
    if type(value.member_kind) is not SourceMemberKind:
        return (SourceMemberIdentityReason.MEMBER_KIND_INVALID,)
    if value.member_kind not in (SourceMemberKind.ENDPOINT_OPERATION, SourceMemberKind.ARTIFACT_MEMBER):
        return (SourceMemberIdentityReason.MEMBER_KIND_INVALID,)

    if value.member_kind is SourceMemberKind.ENDPOINT_OPERATION:
        reasons = _validate_endpoint_operation(value)
    else:
        reasons = _validate_artifact_member(value)

    return tuple(sorted(reasons, key=lambda r: r.value))


def is_valid_source_member_identity(value: object) -> bool:
    """Return True if value is a completely valid SourceMemberIdentity."""
    return len(validate_source_member_identity(value)) == 0


def source_member_identity_payload(value: SourceMemberIdentity) -> dict[str, object]:
    """Serialize SourceMemberIdentity to a canonical 5-key dict."""
    return {
        "member_kind": value.member_kind.value,
        "endpoint_code": value.endpoint_code,
        "operation_code": value.operation_code,
        "artifact_code": value.artifact_code,
        "artifact_version": value.artifact_version,
    }


def verify_member_authority_binding(
    *,
    observed: SourceMemberIdentity,
    selected: SourceMemberIdentity,
) -> tuple[SourceMemberIdentityReason, ...]:
    """Verify exact structural and identity binding between observed and selected member identities.

    Authority Boundary:
    This verifier checks only that observed and selected SourceMemberIdentity values are
    each individually valid and share exact 5-field equality. It does NOT verify Decision
    ownership, actual PASS outcome from the REQUEST Guard, or issuer authenticity.
    The designation 'Authorized' is strictly forbidden at this boundary; only 'Verified'
    or exact structural match is asserted.
    """
    reasons: set[SourceMemberIdentityReason] = set()
    reasons.update(validate_source_member_identity(observed))
    reasons.update(validate_source_member_identity(selected))

    if (
        type(observed) is not SourceMemberIdentity
        or type(selected) is not SourceMemberIdentity
        or observed.member_kind != selected.member_kind
        or observed.endpoint_code != selected.endpoint_code
        or observed.operation_code != selected.operation_code
        or observed.artifact_code != selected.artifact_code
        or observed.artifact_version != selected.artifact_version
    ):
        reasons.add(SourceMemberIdentityReason.MEMBER_AUTHORITY_MISMATCH)

    return tuple(sorted(reasons, key=lambda r: r.value))
