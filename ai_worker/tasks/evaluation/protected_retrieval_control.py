from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    ProtectedAction,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    VerifiedAuthorizationApproval,
    _is_valid_database_login,
    _is_valid_dataset_target,
    authorization_grant_approval_sha256,
)
from ai_worker.tasks.evaluation.schemas.common import Sha256Hex, StrictContractModel


class ControlCommandKind(StrEnum):
    INGEST_APPROVAL = "INGEST_APPROVAL"
    GRANT = "GRANT"
    REVOKE = "REVOKE"
    EXPIRE = "EXPIRE"
    REGISTER_IDENTITY = "REGISTER_IDENTITY"
    DISABLE_IDENTITY = "DISABLE_IDENTITY"
    REGISTER_DATASET = "REGISTER_DATASET"
    TRANSITION_DATASET = "TRANSITION_DATASET"
    FREEZE_DATASET = "FREEZE_DATASET"


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


class RegisterIdentityCommand(_ControlCommand):
    database_login: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    actor_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    actor_namespace: Literal["GITHUB_LOGIN", "SERVICE_IDENTITY", "SYSTEM"]
    identity_plane: Literal["DATA", "CONTROL"]
    principal_role: ProtectedPrincipalRole | None = None
    approval_role: ProtectedApprovalRole | None = None
    enabled: Literal[True] = True

    @model_validator(mode="after")
    def validate_plane_roles(self) -> RegisterIdentityCommand:
        if self.identity_plane == "DATA":
            if self.principal_role is None or self.approval_role is not None:
                raise ValueError("DATA plane identity requires principal_role and forbids approval_role")
        elif self.identity_plane == "CONTROL":
            if self.approval_role is None or self.principal_role is not None:
                raise ValueError("CONTROL plane identity requires approval_role and forbids principal_role")
        return self


class DisableIdentityCommand(_ControlCommand):
    database_login: str = Field(min_length=1, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    expected_actor_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    expected_actor_namespace: Literal["GITHUB_LOGIN", "SERVICE_IDENTITY", "SYSTEM"]


class _DatasetControlCommand(_ControlCommand):
    dataset_id: str
    dataset_version: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")

    @field_validator("dataset_id")
    @classmethod
    def require_dataset_uuid_v4(cls, value: str) -> str:
        return _require_uuid_v4(value)


class RegisterDatasetCommand(_DatasetControlCommand):
    binding: ProtectedDatasetBinding
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    hmac_key_version: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_initial_binding(self) -> RegisterDatasetCommand:
        immutable = (
            self.binding.dataset_id,
            self.binding.dataset_version,
            self.binding.manifest_sha256,
            self.binding.protected_artifact_sha256,
            self.binding.hmac_key_version,
        )
        commanded = (
            self.dataset_id,
            self.dataset_version,
            self.manifest_sha256,
            self.protected_artifact_sha256,
            self.hmac_key_version,
        )
        if immutable != commanded:
            raise ValueError("dataset binding does not match registration fields")
        if (
            self.binding.state is not ProtectedDatasetState.ACCESS_AUTHORIZED
            or self.binding.state_revision != 1
            or self.binding.authored_count != 0
            or self.binding.review_complete
            or self.binding.freeze_receipt_ref is not None
        ):
            raise ValueError("dataset binding must use the initial lifecycle")
        return self


class TransitionDatasetCommand(_DatasetControlCommand):
    from_state: ProtectedDatasetState
    to_state: ProtectedDatasetState
    expected_state_revision: int = Field(ge=1)
    authored_count: int = Field(ge=0, le=40)
    review_complete: bool


class FreezeDatasetCommand(_DatasetControlCommand):
    expected_state_revision: int = Field(ge=1)
    approval_source_event_id: str
    expected_raw_sha256: Sha256Hex

    @field_validator("approval_source_event_id")
    @classmethod
    def require_source_uuid_v4(cls, value: str) -> str:
        return _require_uuid_v4(value)


class FreezeApprovalSourceEvidence(StrictContractModel):
    source_event_id: str = Field(min_length=1, max_length=160)
    action: Literal[ProtectedAction.FREEZE]
    dataset_id: str = Field(min_length=1, max_length=160)
    dataset_version: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    authored_count: Literal[40]
    review_complete: Literal[True]
    leakage_axis_intersections: tuple[Literal[0], Literal[0], Literal[0], Literal[0]]
    issuer: ProtectedApprovalPrincipal
    state: Literal["APPROVED"]
    recorded_at: datetime
    target_commit_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_artifact_sha256: Sha256Hex
    canonical_raw_sha256: Sha256Hex
    implementation_participants: tuple[ActorIdentity, ...] = Field(min_length=1)

    @field_validator("source_event_id", "dataset_id")
    @classmethod
    def require_uuid_v4_event_and_dataset(cls, value: str) -> str:
        return _require_uuid_v4(value)

    @field_validator("issuer")
    @classmethod
    def require_product_safety_reviewer(cls, value: ProtectedApprovalPrincipal) -> ProtectedApprovalPrincipal:
        if value.role is not ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER:
            raise ValueError("freeze approval must be issued by a PRODUCT_SAFETY_REVIEWER")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_utc_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("recorded_at must be in UTC")
        return value

    @field_validator("implementation_participants")
    @classmethod
    def require_unique_participants(cls, value: tuple[ActorIdentity, ...]) -> tuple[ActorIdentity, ...]:
        identities = {(item.namespace, item.actor_id) for item in value}
        if len(identities) != len(value):
            raise ValueError("implementation participants must be unique")
        return value


ControlCommand = (
    IngestApprovalCommand
    | GrantAuthorizationCommand
    | RevokeAuthorizationCommand
    | ExpireAuthorizationCommand
    | RegisterIdentityCommand
    | DisableIdentityCommand
    | RegisterDatasetCommand
    | TransitionDatasetCommand
    | FreezeDatasetCommand
)


class ControlCommandResult(StrictContractModel):
    request_id: str
    command_kind: ControlCommandKind
    target_id: str
    effective_revision: int | None = Field(ge=1)
    authorization_audit_event_id: str | None
    reason_code: Literal[
        "APPROVAL_VERIFIED",
        "AUTHORIZED",
        "REVOKED",
        "EXPIRED",
        "IDENTITY_REGISTERED",
        "IDENTITY_DISABLED",
        "DATASET_REGISTERED",
        "DATASET_TRANSITIONED",
        "DATASET_FROZEN",
    ]

    @field_validator("request_id", "authorization_audit_event_id")
    @classmethod
    def require_request_uuid_v4(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_uuid_v4(value)

    @model_validator(mode="after")
    def validate_result_shape(self) -> ControlCommandResult:
        ingest = self.command_kind is ControlCommandKind.INGEST_APPROVAL
        identity = self.command_kind in {
            ControlCommandKind.REGISTER_IDENTITY,
            ControlCommandKind.DISABLE_IDENTITY,
        }
        dataset = self.command_kind in {
            ControlCommandKind.REGISTER_DATASET,
            ControlCommandKind.TRANSITION_DATASET,
            ControlCommandKind.FREEZE_DATASET,
        }
        expected_reason = {
            ControlCommandKind.INGEST_APPROVAL: "APPROVAL_VERIFIED",
            ControlCommandKind.GRANT: "AUTHORIZED",
            ControlCommandKind.REVOKE: "REVOKED",
            ControlCommandKind.EXPIRE: "EXPIRED",
            ControlCommandKind.REGISTER_IDENTITY: "IDENTITY_REGISTERED",
            ControlCommandKind.DISABLE_IDENTITY: "IDENTITY_DISABLED",
            ControlCommandKind.REGISTER_DATASET: "DATASET_REGISTERED",
            ControlCommandKind.TRANSITION_DATASET: "DATASET_TRANSITIONED",
            ControlCommandKind.FREEZE_DATASET: "DATASET_FROZEN",
        }[self.command_kind]
        if self.reason_code != expected_reason:
            raise ValueError("control result reason does not match command kind")
        if not ingest and not identity and not dataset:
            _require_uuid_v4(self.target_id)
        if identity and not _is_valid_database_login(self.target_id):
            raise ValueError("identity control target must be a valid database login")
        if dataset and not _is_valid_dataset_target(self.target_id):
            raise ValueError("dataset control target must be a valid dataset_id:dataset_version")
        if (ingest or identity) and (
            self.effective_revision is not None or self.authorization_audit_event_id is not None
        ):
            raise ValueError("approval ingestion or identity cannot reference an authorization mutation")
        if dataset and (self.effective_revision is None or self.authorization_audit_event_id is not None):
            raise ValueError("dataset control result must carry effective revision and forbid authorization audit ref")
        if not (ingest or identity or dataset) and (
            self.effective_revision is None or self.authorization_audit_event_id is None
        ):
            raise ValueError("authorization mutation result is incomplete")
        return self


class TrustedApprovalSource(Protocol):
    async def fetch(self, source_event_id: str) -> ApprovalSourceEvidence: ...
    async def fetch_freeze(self, source_event_id: str) -> FreezeApprovalSourceEvidence: ...


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


def verify_freeze_approval(
    dataset: ProtectedDatasetBinding,
    evidence: FreezeApprovalSourceEvidence,
    *,
    approval_source_event_id: str,
    expected_raw_sha256: str,
    executor: ProtectedApprovalPrincipal,
) -> None:
    if evidence.source_event_id != approval_source_event_id:
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
    if evidence.action is not ProtectedAction.FREEZE:
        raise ProtectedSecurityError("APPROVAL_ACTION_MISMATCH")
    if executor.role is not ProtectedApprovalRole.DATASET_CUSTODIAN:
        raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
    if evidence.issuer.role is not ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER:
        raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
    if evidence.issuer.actor == executor.actor:
        raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
    if (
        executor.actor in evidence.implementation_participants
        or evidence.issuer.actor in evidence.implementation_participants
    ):
        raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
    if (
        evidence.canonical_raw_sha256 != expected_raw_sha256
        or evidence.dataset_id != dataset.dataset_id
        or evidence.dataset_version != dataset.dataset_version
        or evidence.manifest_sha256 != dataset.manifest_sha256
        or evidence.protected_artifact_sha256 != dataset.protected_artifact_sha256
        or evidence.authored_count != dataset.authored_count
        or evidence.review_complete != dataset.review_complete
        or evidence.leakage_axis_intersections != dataset.leakage_axis_intersections
    ):
        raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")


__all__ = [
    "ApprovalSourceNotFoundError",
    "ControlCommand",
    "ControlCommandKind",
    "ControlCommandResult",
    "DisableIdentityCommand",
    "ExpireAuthorizationCommand",
    "FreezeApprovalSourceEvidence",
    "FreezeDatasetCommand",
    "GrantAuthorizationCommand",
    "IngestApprovalCommand",
    "RegisterDatasetCommand",
    "RegisterIdentityCommand",
    "RevokeAuthorizationCommand",
    "TransitionDatasetCommand",
    "TrustedApprovalSource",
    "control_command_sha256",
    "verify_authorization_approval",
    "verify_freeze_approval",
]
