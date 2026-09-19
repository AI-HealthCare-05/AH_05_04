"""Pure contract for historical production Source Use Approval observations.

This module deliberately does not decide whether a source is current, fresh, active, or
eligible for a citation member.  It only models the exact source approval identity and the
time/revocation facts persisted by the Source Use Approval authority.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from rag_runtime.runtime_environment import RuntimeEnvironmentCode


class SourceUsePurpose(StrEnum):
    PRODUCT_IDENTIFICATION = "PRODUCT_IDENTIFICATION"
    SAFETY_ROUTING = "SAFETY_ROUTING"
    RULE_DERIVATION = "RULE_DERIVATION"
    RETRIEVAL = "RETRIEVAL"
    PATIENT_CITATION = "PATIENT_CITATION"


class SourceUseApprovalValidationError(ValueError):
    """The Source Use Approval identity or observation is not canonical."""


@runtime_checkable
class SourceUseApprovalReaderPort(Protocol):
    """Read-only consumer port implemented by production adapters."""

    async def read_exact(self, identity: "SourceUseApprovalIdentity") -> "SourceUseApprovalObservation | None": ...

    async def read_usable_exact(
        self,
        identity: "SourceUseApprovalIdentity",
        *,
        evaluation_time: datetime,
    ) -> "SourceUseApprovalObservation | None": ...


def _require_uuid(value: object, field_name: str) -> UUID:
    if type(value) is not UUID:
        raise SourceUseApprovalValidationError(f"{field_name} must be a UUID")
    return value


def _require_nonblank_exact(value: object, field_name: str) -> str:
    if type(value) is not str or not value or not value.strip() or value != value.strip():
        raise SourceUseApprovalValidationError(f"{field_name} must be a nonblank canonical string")
    return value


def _require_aware_datetime(value: object, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise SourceUseApprovalValidationError(f"{field_name} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class SourceUseApprovalIdentity:
    """Exact immutable lookup identity; this is not a content-addressed artifact reference."""

    source_snapshot_id: UUID
    source_code: str
    source_version: str
    environment: RuntimeEnvironmentCode
    purpose: SourceUsePurpose
    approval_version: str

    def __post_init__(self) -> None:
        _require_uuid(self.source_snapshot_id, "source_snapshot_id")
        _require_nonblank_exact(self.source_code, "source_code")
        _require_nonblank_exact(self.source_version, "source_version")
        _require_nonblank_exact(self.approval_version, "approval_version")
        if type(self.environment) is not RuntimeEnvironmentCode:
            raise SourceUseApprovalValidationError("environment must be RuntimeEnvironmentCode")
        if type(self.purpose) is not SourceUsePurpose:
            raise SourceUseApprovalValidationError("purpose must be SourceUsePurpose")


@dataclass(frozen=True, slots=True)
class SourceUseApprovalObservation:
    """Persisted approval facts returned by backend and read-only worker consumers."""

    id: UUID
    identity: SourceUseApprovalIdentity
    valid_from: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoked_by: UUID | None
    revoked_reason: str | None
    actor_id: UUID
    evidence_ref: str

    def __post_init__(self) -> None:
        _require_uuid(self.id, "id")
        if type(self.identity) is not SourceUseApprovalIdentity:
            raise SourceUseApprovalValidationError("identity must be SourceUseApprovalIdentity")
        valid_from = _require_aware_datetime(self.valid_from, "valid_from")
        expires_at = _require_aware_datetime(self.expires_at, "expires_at")
        if expires_at <= valid_from:
            raise SourceUseApprovalValidationError("expires_at must be later than valid_from")
        if self.revoked_at is not None:
            _require_aware_datetime(self.revoked_at, "revoked_at")
        if self.revoked_by is not None:
            _require_uuid(self.revoked_by, "revoked_by")
        if self.revoked_reason is not None:
            _require_nonblank_exact(self.revoked_reason, "revoked_reason")
        revoked_values_present = (
            self.revoked_at is not None and self.revoked_by is not None and self.revoked_reason is not None
        )
        revoked_values_absent = self.revoked_at is None and self.revoked_by is None and self.revoked_reason is None
        if not (revoked_values_present or revoked_values_absent):
            raise SourceUseApprovalValidationError("revocation fields must be all present or all absent")
        _require_uuid(self.actor_id, "actor_id")
        _require_nonblank_exact(self.evidence_ref, "evidence_ref")

    def is_usable_at(self, evaluation_time: datetime) -> bool:
        """Evaluate only validity and revocation; currentness is outside this contract."""

        evaluation_time = _require_aware_datetime(evaluation_time, "evaluation_time")
        return self.valid_from <= evaluation_time < self.expires_at and self.revoked_at is None

    def semantic_payload(self) -> tuple[object, ...]:
        """Return immutable semantic content, excluding row identity and insertion time."""

        return (
            self.identity,
            self.valid_from,
            self.expires_at,
            self.revoked_at,
            self.revoked_by,
            self.revoked_reason,
            self.actor_id,
            self.evidence_ref,
        )


__all__ = [
    "SourceUseApprovalIdentity",
    "SourceUseApprovalObservation",
    "SourceUseApprovalReaderPort",
    "SourceUseApprovalValidationError",
    "SourceUsePurpose",
]
