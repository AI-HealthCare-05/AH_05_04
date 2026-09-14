from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from ai_worker.tasks.evaluation.protected_retrieval import (
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    ProtectedAction,
    ProtectedApprovalRole,
    ProtectedAuthorizationGrant,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    VerifiedAuthorizationApproval,
    authorization_grant_approval_sha256,
)
from ai_worker.tasks.evaluation.schemas.common import Sha256Hex, StrictContractModel


class ControlCommandKind(StrEnum):
    INGEST_APPROVAL = "INGEST_APPROVAL"
    GRANT = "GRANT"
    REVOKE = "REVOKE"
    EXPIRE = "EXPIRE"


def _require_uuid_v4(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("control identifier must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("control identifier must be a UUIDv4")
    return value


class _ControlCommand(StrictContractModel):
    request_id: str

    @field_validator("request_id")
    @classmethod
    def require_request_uuid_v4(cls, value: str) -> str:
        return _require_uuid_v4(value)


class IngestApprovalCommand(_ControlCommand):
    source_event_id: str = Field(min_length=1, max_length=160)
    expected_raw_sha256: Sha256Hex


class GrantAuthorizationCommand(_ControlCommand):
    grant: ProtectedAuthorizationGrant
    expected_dataset_state_revision: int = Field(ge=1)


class _GrantControlCommand(_ControlCommand):
    grant_id: str

    @field_validator("grant_id")
    @classmethod
    def require_grant_uuid_v4(cls, value: str) -> str:
        return _require_uuid_v4(value)


class RevokeAuthorizationCommand(_GrantControlCommand):
    approval_source_event_id: str = Field(min_length=1, max_length=160)
    expected_raw_sha256: Sha256Hex
    expected_effective_revision: int = Field(ge=1)


class ExpireAuthorizationCommand(_GrantControlCommand):
    expected_effective_revision: int = Field(ge=1)


ControlCommand = (
    IngestApprovalCommand | GrantAuthorizationCommand | RevokeAuthorizationCommand | ExpireAuthorizationCommand
)


class ControlCommandResult(StrictContractModel):
    request_id: str
    command_kind: ControlCommandKind
    target_id: str
    effective_revision: int | None = Field(ge=1)
    authorization_audit_event_id: str | None
    reason_code: Literal["APPROVAL_VERIFIED", "AUTHORIZED", "REVOKED", "EXPIRED"]

    @field_validator("request_id", "authorization_audit_event_id")
    @classmethod
    def require_request_uuid_v4(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_uuid_v4(value)

    @model_validator(mode="after")
    def validate_result_shape(self) -> ControlCommandResult:
        ingest = self.command_kind is ControlCommandKind.INGEST_APPROVAL
        expected_reason = {
            ControlCommandKind.INGEST_APPROVAL: "APPROVAL_VERIFIED",
            ControlCommandKind.GRANT: "AUTHORIZED",
            ControlCommandKind.REVOKE: "REVOKED",
            ControlCommandKind.EXPIRE: "EXPIRED",
        }[self.command_kind]
        if self.reason_code != expected_reason:
            raise ValueError("control result reason does not match command kind")
        if not ingest:
            _require_uuid_v4(self.target_id)
        if ingest and (self.effective_revision is not None or self.authorization_audit_event_id is not None):
            raise ValueError("approval ingestion cannot reference an authorization mutation")
        if not ingest and (self.effective_revision is None or self.authorization_audit_event_id is None):
            raise ValueError("authorization mutation result is incomplete")
        return self


class TrustedApprovalSource(Protocol):
    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence: ...


class ApprovalSourceNotFoundError(Exception):
    """Trusted source reports that an approval event does not exist."""


def control_command_sha256(kind: ControlCommandKind, command: ControlCommand) -> str:
    payload: JsonValue = {
        "command_kind": kind.value,
        "command": command.model_dump(mode="json"),
    }
    return sha256(canonical_json_bytes(payload)).hexdigest()


def verify_authorization_approval(
    grant: ProtectedAuthorizationGrant,
    evidence: ApprovalSourceEvidence,
    action: AuthorizationAuditAction,
    expected_raw_sha256: str,
) -> VerifiedAuthorizationApproval:
    if evidence.source_event_id != (
        grant.approval_source_event_id if action is AuthorizationAuditAction.GRANT else evidence.source_event_id
    ):
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
    if evidence.authorization_action is not action:
        raise ProtectedSecurityError("APPROVAL_ACTION_MISMATCH")
    binding = grant.control_implementation
    if (
        evidence.state != "APPROVED"
        or evidence.issuer != grant.issuer
        or evidence.target_commit_oid != binding.commit_oid
        or evidence.target_artifact_sha256 != binding.artifact_sha256
        or evidence.canonical_raw_sha256 != expected_raw_sha256
        or evidence.implementation_participants != binding.participants
    ):
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
    if evidence.approved_grant_payload_sha256 != authorization_grant_approval_sha256(grant):
        raise ProtectedSecurityError("APPROVAL_GRANT_BINDING_MISMATCH")
    if evidence.issuer.actor == grant.subject.actor or evidence.issuer.actor in binding.participants:
        raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
    expected_issuer_role = (
        ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER
        if grant.subject.role is ProtectedPrincipalRole.DATASET_CUSTODIAN
        else ProtectedApprovalRole.DATASET_CUSTODIAN
    )
    if evidence.issuer.role is not expected_issuer_role:
        raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
    allowed_actions = {
        ProtectedPrincipalRole.HOLDOUT_AUTHOR: {ProtectedAction.READ, ProtectedAction.WRITE},
        ProtectedPrincipalRole.DATASET_CUSTODIAN: {ProtectedAction.READ, ProtectedAction.FREEZE},
        ProtectedPrincipalRole.PROTECTED_RUNNER: {ProtectedAction.READ, ProtectedAction.RUN},
    }
    if not set(grant.actions) <= allowed_actions[grant.subject.role]:
        raise ProtectedSecurityError("ACTION_NOT_GRANTED")
    return VerifiedAuthorizationApproval._from_verified(evidence, grant, action)


__all__ = [
    "ApprovalSourceNotFoundError",
    "ControlCommand",
    "ControlCommandKind",
    "ControlCommandResult",
    "ExpireAuthorizationCommand",
    "GrantAuthorizationCommand",
    "IngestApprovalCommand",
    "RevokeAuthorizationCommand",
    "TrustedApprovalSource",
    "control_command_sha256",
    "verify_authorization_approval",
]
