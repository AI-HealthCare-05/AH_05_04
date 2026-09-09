from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Protocol, Self
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.schemas.common import Sha256Hex, StrictContractModel

_SAFE_REASON_CODES = frozenset(
    {
        "ACTION_NOT_GRANTED",
        "APPROVAL_ACTION_MISMATCH",
        "APPROVAL_EVIDENCE_MISMATCH",
        "APPROVAL_GRANT_BINDING_MISMATCH",
        "APPROVAL_NOT_VERIFIED",
        "AUDIT_BINDING_MISMATCH",
        "AUDIT_CAS_CONFLICT",
        "AUDIT_HASH_MISMATCH",
        "AUDIT_TAIL_TRUNCATED",
        "AUDIT_TRANSITION_INVALID",
        "AUDIT_UNAVAILABLE",
        "AUTHORIZATION_EXPIRED",
        "AUTHORIZATION_NOT_FOUND",
        "AUTHORIZATION_REVOKED",
        "AUTHORIZATION_REVISION_MISMATCH",
        "CAPABILITY_ALREADY_CONSUMED",
        "CAPABILITY_BINDING_MISMATCH",
        "CAPABILITY_EXPIRED",
        "DATASET_BINDING_MISMATCH",
        "DATASET_STATE_MISMATCH",
        "FREEZE_EVIDENCE_INCOMPLETE",
        "GRANT_SUBJECT_MISMATCH",
        "GUARD_BINDING_MISMATCH",
        "INTERNAL_ERROR",
        "ISSUER_ROLE_DENIED",
        "OPERATION_OUTCOME_UNKNOWN",
        "RECONCILIATION_REQUIRED",
        "ROLE_ACTION_STATE_DENIED",
        "RUN_EVIDENCE_INCOMPLETE",
        "SELF_APPROVAL_DENIED",
    }
)


class ProtectedSecurityError(RuntimeError):
    """A fail-closed error containing only a fixed, non-sensitive reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code if reason_code in _SAFE_REASON_CODES else "INTERNAL_ERROR"
        super().__init__(self.reason_code)


class ProtectedAction(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    FREEZE = "FREEZE"
    RUN = "RUN"


class ProtectedPrincipalRole(StrEnum):
    HOLDOUT_AUTHOR = "HOLDOUT_AUTHOR"
    DATASET_CUSTODIAN = "DATASET_CUSTODIAN"
    PROTECTED_RUNNER = "PROTECTED_RUNNER"


class ProtectedApprovalRole(StrEnum):
    DATASET_CUSTODIAN = "DATASET_CUSTODIAN"
    PRODUCT_SAFETY_REVIEWER = "PRODUCT_SAFETY_REVIEWER"


class ProtectedDatasetState(StrEnum):
    ACCESS_AUTHORIZED = "ACCESS_AUTHORIZED"
    AUTHORING = "AUTHORING"
    REVIEW_READY = "REVIEW_READY"
    FROZEN = "FROZEN"


class OpaqueRefNamespace(StrEnum):
    REQUEST = "REQUEST"
    DATASET = "DATASET"
    HOLDOUT_SET = "HOLDOUT_SET"
    RUN_RESULT = "RUN_RESULT"
    AUDIT_EVENT = "AUDIT_EVENT"


class ProtectedAuditEventKind(StrEnum):
    AUTHORIZATION = "AUTHORIZATION"
    OPERATION = "OPERATION"


class AuthorizationAuditAction(StrEnum):
    GRANT = "GRANT"
    REVOKE = "REVOKE"
    EXPIRE = "EXPIRE"


class OperationAuditOutcome(StrEnum):
    DENIED = "DENIED"
    INTENT = "INTENT"
    SUCCEEDED = "SUCCEEDED"
    UNKNOWN = "UNKNOWN"


class ProtectedAuditReason(StrEnum):
    ACTION_NOT_GRANTED = "ACTION_NOT_GRANTED"
    APPROVAL_ACTION_MISMATCH = "APPROVAL_ACTION_MISMATCH"
    APPROVAL_EVIDENCE_MISMATCH = "APPROVAL_EVIDENCE_MISMATCH"
    APPROVAL_GRANT_BINDING_MISMATCH = "APPROVAL_GRANT_BINDING_MISMATCH"
    APPROVAL_NOT_VERIFIED = "APPROVAL_NOT_VERIFIED"
    APPROVAL_VERIFIED = "APPROVAL_VERIFIED"
    AUDIT_BINDING_MISMATCH = "AUDIT_BINDING_MISMATCH"
    AUDIT_CAS_CONFLICT = "AUDIT_CAS_CONFLICT"
    AUDIT_HASH_MISMATCH = "AUDIT_HASH_MISMATCH"
    AUDIT_TAIL_TRUNCATED = "AUDIT_TAIL_TRUNCATED"
    AUDIT_TRANSITION_INVALID = "AUDIT_TRANSITION_INVALID"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    AUTHORIZATION_NOT_FOUND = "AUTHORIZATION_NOT_FOUND"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    AUTHORIZATION_REVISION_MISMATCH = "AUTHORIZATION_REVISION_MISMATCH"
    AUTHORIZED = "AUTHORIZED"
    CAPABILITY_ALREADY_CONSUMED = "CAPABILITY_ALREADY_CONSUMED"
    CAPABILITY_BINDING_MISMATCH = "CAPABILITY_BINDING_MISMATCH"
    CAPABILITY_EXPIRED = "CAPABILITY_EXPIRED"
    COMPLETED = "COMPLETED"
    DATASET_BINDING_MISMATCH = "DATASET_BINDING_MISMATCH"
    DATASET_STATE_MISMATCH = "DATASET_STATE_MISMATCH"
    FREEZE_EVIDENCE_INCOMPLETE = "FREEZE_EVIDENCE_INCOMPLETE"
    GRANT_SUBJECT_MISMATCH = "GRANT_SUBJECT_MISMATCH"
    GUARD_BINDING_MISMATCH = "GUARD_BINDING_MISMATCH"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    ISSUER_ROLE_DENIED = "ISSUER_ROLE_DENIED"
    OPERATION_OUTCOME_UNKNOWN = "OPERATION_OUTCOME_UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    REVOKED = "REVOKED"
    ROLE_ACTION_STATE_DENIED = "ROLE_ACTION_STATE_DENIED"
    RUN_EVIDENCE_INCOMPLETE = "RUN_EVIDENCE_INCOMPLETE"
    SELF_APPROVAL_DENIED = "SELF_APPROVAL_DENIED"
    TERMINAL_AUDIT_UNCERTAIN = "TERMINAL_AUDIT_UNCERTAIN"
    EXPIRED = "EXPIRED"


class ActorIdentity(StrictContractModel):
    actor_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    namespace: Literal["GITHUB_LOGIN", "SERVICE_IDENTITY", "SYSTEM"]


class ProtectedPrincipal(StrictContractModel):
    actor: ActorIdentity
    role: ProtectedPrincipalRole


class ProtectedApprovalPrincipal(StrictContractModel):
    actor: ActorIdentity
    role: ProtectedApprovalRole


class OpaqueLogicalRef(StrictContractModel):
    namespace: OpaqueRefNamespace
    value: str

    @field_validator("value")
    @classmethod
    def require_uuid_v4(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ValueError("opaque logical ref must be a UUIDv4") from error
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("opaque logical ref must be a UUIDv4")
        return value


class ControlImplementationBinding(StrictContractModel):
    commit_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_sha256: Sha256Hex
    participants: tuple[ActorIdentity, ...] = Field(min_length=1)

    @field_validator("participants")
    @classmethod
    def require_unique_participants(cls, value: tuple[ActorIdentity, ...]) -> tuple[ActorIdentity, ...]:
        identities = {(item.namespace, item.actor_id) for item in value}
        if len(identities) != len(value):
            raise ValueError("implementation participants must be unique")
        return value


class ApprovalSourceEvidence(StrictContractModel):
    source_event_id: str = Field(min_length=1, max_length=160)
    authorization_action: AuthorizationAuditAction
    approved_grant_payload_sha256: Sha256Hex
    issuer: ProtectedApprovalPrincipal
    state: Literal["APPROVED"]
    recorded_at: datetime
    target_commit_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_artifact_sha256: Sha256Hex
    canonical_raw_sha256: Sha256Hex
    implementation_participants: tuple[ActorIdentity, ...] = Field(min_length=1)

    @field_validator("authorization_action")
    @classmethod
    def reject_nonapproval_action(cls, value: AuthorizationAuditAction) -> AuthorizationAuditAction:
        if value is AuthorizationAuditAction.EXPIRE:
            raise ValueError("expiry is not an approval action")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("approval timestamp must be UTC-aware")
        return value.astimezone(UTC)


class ProtectedDatasetBinding(StrictContractModel):
    dataset_id: str = Field(min_length=1, max_length=160)
    dataset_version: str = Field(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    hmac_key_version: str = Field(min_length=1, max_length=160)
    state: ProtectedDatasetState
    state_revision: int = Field(ge=1)
    authored_count: int = Field(ge=0, le=40)
    review_complete: bool
    leakage_axis_intersections: tuple[int, int, int, int] | None = None
    freeze_receipt_ref: OpaqueLogicalRef | None = None
    execution_authorization_ref: OpaqueLogicalRef | None = None
    retriever_binding_ref: OpaqueLogicalRef | None = None

    @field_validator("leakage_axis_intersections")
    @classmethod
    def require_nonnegative_axes(cls, value: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
        if value is not None and any(item < 0 for item in value):
            raise ValueError("leakage intersections must be non-negative")
        return value


class ProtectedAuthorizationGrant(StrictContractModel):
    grant_id: str
    revision: int = Field(ge=1)
    subject: ProtectedPrincipal
    dataset_id: str
    dataset_version: str
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    hmac_key_version: str
    actions: tuple[ProtectedAction, ...] = Field(min_length=1)
    issuer: ProtectedApprovalPrincipal
    control_implementation: ControlImplementationBinding
    approval_source_event_id: str
    approval_source_raw_sha256: Sha256Hex
    valid_from: datetime
    expires_at: datetime

    @field_validator("grant_id")
    @classmethod
    def require_grant_uuid_v4(cls, value: str) -> str:
        if UUID(value).version != 4 or str(UUID(value)) != value:
            raise ValueError("grant ID must be a UUIDv4")
        return value

    @model_validator(mode="after")
    def validate_window_and_actions(self) -> Self:
        if self.valid_from.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("grant timestamps must be timezone-aware")
        if self.valid_from.utcoffset() != timedelta(0) or self.expires_at.utcoffset() != timedelta(0):
            raise ValueError("grant timestamps must use UTC")
        if self.valid_from >= self.expires_at:
            raise ValueError("grant validity window must be increasing")
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("grant actions must be unique")
        return self


class ProtectedOperationRequest(StrictContractModel):
    request_id: str
    operation_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    dataset: ProtectedDatasetBinding
    target_ref: OpaqueLogicalRef
    action: ProtectedAction
    principal: ProtectedPrincipal

    @field_validator("request_id")
    @classmethod
    def require_request_uuid_v4(cls, value: str) -> str:
        if UUID(value).version != 4 or str(UUID(value)) != value:
            raise ValueError("request ID must be a UUIDv4")
        return value


class ProtectedAuthorizationCapability(StrictContractModel):
    request_id: str
    grant_id: str
    grant_revision: int
    dataset_state_revision: int
    protected_artifact_sha256: Sha256Hex
    action: ProtectedAction
    target_ref: OpaqueLogicalRef
    nonce: str
    expires_at: datetime


class ProtectedOperationResult(StrictContractModel):
    result_ref: OpaqueLogicalRef
    reason_code: Literal["PROTECTED_OPERATION_SUCCEEDED"]


class AuthorizationAuditEntry(StrictContractModel):
    event_kind: Literal[ProtectedAuditEventKind.AUTHORIZATION]
    sequence: int = Field(ge=1)
    event_id: str
    grant_id: str
    grant_revision: int
    effective_revision: int
    subject: ProtectedPrincipal
    issuer: ProtectedApprovalPrincipal
    dataset_id: str
    dataset_version: str
    manifest_sha256: Sha256Hex
    protected_artifact_sha256: Sha256Hex
    hmac_key_version: str
    actions: tuple[ProtectedAction, ...]
    control_implementation: ControlImplementationBinding
    approval_source_event_id: str
    approval_source_raw_sha256: Sha256Hex
    valid_from: datetime
    expires_at: datetime
    action: AuthorizationAuditAction
    reason_code: ProtectedAuditReason
    recorded_at: datetime
    previous_entry_sha256: Sha256Hex | None
    entry_sha256: Sha256Hex


class OperationAuditEntry(StrictContractModel):
    event_kind: Literal[ProtectedAuditEventKind.OPERATION]
    sequence: int = Field(ge=1)
    event_id: str
    operation_key: str
    request_id: str
    principal: ProtectedPrincipal
    protected_action: ProtectedAction
    target_ref: OpaqueLogicalRef
    dataset_id: str
    dataset_version: str
    manifest_sha256: Sha256Hex
    hmac_key_version: str
    grant_id: str | None
    grant_revision: int | None
    dataset_state_revision: int
    protected_artifact_sha256: Sha256Hex
    capability_nonce: str | None
    result_ref: OpaqueLogicalRef | None
    closes_intent: bool
    outcome: OperationAuditOutcome
    reason_code: ProtectedAuditReason
    recorded_at: datetime
    previous_entry_sha256: Sha256Hex | None
    entry_sha256: Sha256Hex


ProtectedAuditEntry = AuthorizationAuditEntry | OperationAuditEntry


class _VerificationSeal:
    pass


_VERIFICATION_SEAL = _VerificationSeal()


class VerifiedAuthorizationApproval:
    __slots__ = ("action", "evidence", "grant_sha256", "_seal")

    def __init__(
        self,
        evidence: ApprovalSourceEvidence,
        grant_sha256: str,
        action: AuthorizationAuditAction,
        seal: _VerificationSeal,
    ) -> None:
        if seal is not _VERIFICATION_SEAL:
            raise ProtectedSecurityError("APPROVAL_NOT_VERIFIED")
        self.evidence = evidence
        self.grant_sha256 = grant_sha256
        self.action = action
        self._seal = seal

    @classmethod
    def _from_verified(
        cls,
        evidence: ApprovalSourceEvidence,
        grant: ProtectedAuthorizationGrant,
        action: AuthorizationAuditAction,
    ) -> VerifiedAuthorizationApproval:
        return cls(evidence, authorization_grant_sha256(grant), action, _VERIFICATION_SEAL)

    def is_verified(self) -> bool:
        return self._seal is _VERIFICATION_SEAL


class TrustedClock(Protocol):
    def now_utc(self) -> datetime: ...


class AuthorizationLedger(Protocol):
    async def find_for(self, request: ProtectedOperationRequest) -> ProtectedAuthorizationGrant | None: ...

    async def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant: ...

    def require_dataset(self, request: ProtectedOperationRequest) -> ProtectedDatasetBinding: ...


class GuardSession(Protocol):
    def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant: ...

    def issue_capability(
        self, request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant
    ) -> ProtectedAuthorizationCapability: ...

    def consume(self, capability: ProtectedAuthorizationCapability) -> None: ...


class AuthorizationGuard(Protocol):
    def hold(
        self, request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant
    ) -> AbstractAsyncContextManager[GuardSession]: ...


class ProtectedAuditJournal(Protocol):
    def operation_history(self, request: ProtectedOperationRequest) -> tuple[OperationAuditEntry, ...]: ...

    def append_operation(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant | None,
        outcome: OperationAuditOutcome,
        reason_code: ProtectedAuditReason | str,
        capability: ProtectedAuthorizationCapability | None = None,
        result: ProtectedOperationResult | None = None,
        *,
        closes_intent: bool = False,
    ) -> OperationAuditEntry: ...


class ProtectedOperation(Protocol):
    async def execute(
        self, request: ProtectedOperationRequest, capability: ProtectedAuthorizationCapability
    ) -> ProtectedOperationResult: ...


def _validate_grant(request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant, now: datetime) -> None:
    if request.principal != grant.subject:
        raise ProtectedSecurityError("GRANT_SUBJECT_MISMATCH")
    dataset = request.dataset
    if (
        dataset.dataset_id != grant.dataset_id
        or dataset.dataset_version != grant.dataset_version
        or dataset.manifest_sha256 != grant.manifest_sha256
        or dataset.protected_artifact_sha256 != grant.protected_artifact_sha256
        or dataset.hmac_key_version != grant.hmac_key_version
    ):
        raise ProtectedSecurityError("DATASET_BINDING_MISMATCH")
    if request.action not in grant.actions:
        raise ProtectedSecurityError("ACTION_NOT_GRANTED")
    if not grant.valid_from <= now < grant.expires_at:
        raise ProtectedSecurityError("AUTHORIZATION_EXPIRED")


def _validate_role_state(request: ProtectedOperationRequest) -> None:
    role = request.principal.role
    action = request.action
    dataset = request.dataset
    allowed = {
        (ProtectedPrincipalRole.HOLDOUT_AUTHOR, ProtectedAction.READ): {
            ProtectedDatasetState.ACCESS_AUTHORIZED,
            ProtectedDatasetState.AUTHORING,
        },
        (ProtectedPrincipalRole.HOLDOUT_AUTHOR, ProtectedAction.WRITE): {
            ProtectedDatasetState.ACCESS_AUTHORIZED,
            ProtectedDatasetState.AUTHORING,
        },
        (ProtectedPrincipalRole.DATASET_CUSTODIAN, ProtectedAction.READ): set(ProtectedDatasetState),
        (ProtectedPrincipalRole.DATASET_CUSTODIAN, ProtectedAction.FREEZE): {ProtectedDatasetState.REVIEW_READY},
        (ProtectedPrincipalRole.PROTECTED_RUNNER, ProtectedAction.READ): {ProtectedDatasetState.FROZEN},
        (ProtectedPrincipalRole.PROTECTED_RUNNER, ProtectedAction.RUN): {ProtectedDatasetState.FROZEN},
    }
    if dataset.state not in allowed.get((role, action), set()):
        raise ProtectedSecurityError("ROLE_ACTION_STATE_DENIED")
    if action is ProtectedAction.FREEZE and not (
        dataset.authored_count == 40 and dataset.review_complete and dataset.leakage_axis_intersections == (0, 0, 0, 0)
    ):
        raise ProtectedSecurityError("FREEZE_EVIDENCE_INCOMPLETE")
    if (
        role is ProtectedPrincipalRole.PROTECTED_RUNNER
        and action in {ProtectedAction.READ, ProtectedAction.RUN}
        and not (
            dataset.freeze_receipt_ref is not None
            and dataset.execution_authorization_ref is not None
            and dataset.retriever_binding_ref is not None
        )
    ):
        raise ProtectedSecurityError("RUN_EVIDENCE_INCOMPLETE")


def _validate_completed_operation_replay(
    request: ProtectedOperationRequest,
    dataset: ProtectedDatasetBinding,
    entry: OperationAuditEntry,
) -> None:
    if (
        request.principal != entry.principal
        or request.action is not entry.protected_action
        or request.target_ref != entry.target_ref
        or dataset.state_revision != entry.dataset_state_revision
        or dataset.protected_artifact_sha256 != entry.protected_artifact_sha256
    ):
        raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")


def _deny(
    journal: ProtectedAuditJournal,
    request: ProtectedOperationRequest,
    grant: ProtectedAuthorizationGrant | None,
    reason: str,
    *,
    closes_intent: bool = False,
) -> None:
    journal.append_operation(
        request,
        grant,
        OperationAuditOutcome.DENIED,
        ProtectedAuditReason(reason),
        closes_intent=closes_intent,
    )


def _operation_lifecycle_terminal(
    history: tuple[OperationAuditEntry, ...],
) -> OperationAuditEntry | None:
    lifecycle = [entry for entry in history if entry.outcome is not OperationAuditOutcome.DENIED or entry.closes_intent]
    return lifecycle[-1] if lifecycle else None


def _validated_replay_result(
    request: ProtectedOperationRequest,
    authoritative_dataset: ProtectedDatasetBinding,
    terminal_entry: OperationAuditEntry,
    replay_grant: ProtectedAuthorizationGrant,
    now: datetime,
) -> ProtectedOperationResult:
    if terminal_entry.result_ref is None:
        raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
    authorized_request = request.model_copy(update={"dataset": authoritative_dataset})
    _validate_completed_operation_replay(authorized_request, authoritative_dataset, terminal_entry)
    _validate_role_state(authorized_request)
    if terminal_entry.grant_id != replay_grant.grant_id:
        raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
    _validate_grant(authorized_request, replay_grant, now)
    return ProtectedOperationResult(
        result_ref=terminal_entry.result_ref,
        reason_code="PROTECTED_OPERATION_SUCCEEDED",
    )


async def _replay_completed_operation(
    request: ProtectedOperationRequest,
    terminal_entry: OperationAuditEntry,
    ledger: AuthorizationLedger,
    clock: TrustedClock,
) -> ProtectedOperationResult:
    authoritative_dataset = ledger.require_dataset(request)
    if terminal_entry.grant_id is None:
        raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
    replay_grant = await ledger.require_current(terminal_entry.grant_id)
    return _validated_replay_result(
        request,
        authoritative_dataset,
        terminal_entry,
        replay_grant,
        clock.now_utc(),
    )


async def _resolve_operation_history(
    request: ProtectedOperationRequest,
    history: tuple[OperationAuditEntry, ...],
    ledger: AuthorizationLedger,
    clock: TrustedClock,
    *,
    intent_requires_reconciliation: bool,
) -> ProtectedOperationResult | None:
    if not history:
        return None
    terminal_entry = _operation_lifecycle_terminal(history)
    if terminal_entry is None:
        return None
    if terminal_entry.outcome is OperationAuditOutcome.UNKNOWN or (
        terminal_entry.outcome is OperationAuditOutcome.INTENT and intent_requires_reconciliation
    ):
        raise ProtectedSecurityError("RECONCILIATION_REQUIRED")
    if terminal_entry.outcome is OperationAuditOutcome.SUCCEEDED:
        return await _replay_completed_operation(request, terminal_entry, ledger, clock)
    return None


def _resolve_guarded_operation_history(
    request: ProtectedOperationRequest,
    history: tuple[OperationAuditEntry, ...],
    ledger: AuthorizationLedger,
    session: GuardSession,
    clock: TrustedClock,
) -> ProtectedOperationResult | None:
    if not history:
        return None
    terminal_entry = _operation_lifecycle_terminal(history)
    if terminal_entry is None:
        return None
    if terminal_entry.outcome in {OperationAuditOutcome.INTENT, OperationAuditOutcome.UNKNOWN}:
        raise ProtectedSecurityError("RECONCILIATION_REQUIRED")
    if terminal_entry.outcome is OperationAuditOutcome.SUCCEEDED:
        if terminal_entry.grant_id is None:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        replay_grant = session.require_current(terminal_entry.grant_id)
        return _validated_replay_result(
            request,
            ledger.require_dataset(request),
            terminal_entry,
            replay_grant,
            clock.now_utc(),
        )
    return None


async def execute_protected_operation(
    request: ProtectedOperationRequest,
    *,
    ledger: AuthorizationLedger,
    guard: AuthorizationGuard,
    journal: ProtectedAuditJournal,
    operation: ProtectedOperation,
    clock: TrustedClock,
) -> ProtectedOperationResult:
    grant: ProtectedAuthorizationGrant | None = None
    operation_started = False
    try:
        replay = await _resolve_operation_history(
            request,
            journal.operation_history(request),
            ledger,
            clock,
            intent_requires_reconciliation=False,
        )
        if replay is not None:
            return replay

        grant = await ledger.find_for(request)
        authoritative_dataset = ledger.require_dataset(request)
        authoritative_request = request.model_copy(update={"dataset": authoritative_dataset})
        _validate_role_state(authoritative_request)
        if grant is None:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        grant = await ledger.require_current(grant.grant_id)
        _validate_grant(authoritative_request, grant, clock.now_utc())
        async with guard.hold(authoritative_request, grant) as session:
            replay = _resolve_guarded_operation_history(
                authoritative_request,
                journal.operation_history(authoritative_request),
                ledger,
                session,
                clock,
            )
            if replay is not None:
                return replay
            journal.append_operation(
                authoritative_request,
                grant,
                OperationAuditOutcome.INTENT,
                ProtectedAuditReason.AUTHORIZED,
            )
            capability = session.issue_capability(authoritative_request, grant)
            session.consume(capability)
            try:
                operation_started = True
                result = await operation.execute(authoritative_request, capability)
            except Exception:
                journal.append_operation(
                    authoritative_request,
                    grant,
                    OperationAuditOutcome.UNKNOWN,
                    ProtectedAuditReason.OPERATION_OUTCOME_UNKNOWN,
                    capability,
                )
                raise ProtectedSecurityError("OPERATION_OUTCOME_UNKNOWN") from None
            try:
                journal.append_operation(
                    authoritative_request,
                    grant,
                    OperationAuditOutcome.SUCCEEDED,
                    ProtectedAuditReason.COMPLETED,
                    capability,
                    result,
                )
            except ProtectedSecurityError:
                journal.append_operation(
                    authoritative_request,
                    grant,
                    OperationAuditOutcome.UNKNOWN,
                    ProtectedAuditReason.TERMINAL_AUDIT_UNCERTAIN,
                    capability,
                )
                raise ProtectedSecurityError("OPERATION_OUTCOME_UNKNOWN") from None
            return result
    except ProtectedSecurityError as error:
        denial_history = journal.operation_history(request)
        if error.reason_code != "OPERATION_OUTCOME_UNKNOWN" and not operation_started:
            lifecycle_terminal = _operation_lifecycle_terminal(denial_history)
            _deny(
                journal,
                request,
                grant,
                error.reason_code,
                closes_intent=(
                    error.reason_code != "RECONCILIATION_REQUIRED"
                    and lifecycle_terminal is not None
                    and lifecycle_terminal.outcome is OperationAuditOutcome.INTENT
                    and lifecycle_terminal.request_id == request.request_id
                ),
            )
        raise


def audit_entry_sha256(entry: ProtectedAuditEntry) -> str:
    payload = entry.model_dump(mode="json", exclude={"entry_sha256"})
    return sha256(canonical_json_bytes(payload)).hexdigest()


def authorization_grant_sha256(grant: ProtectedAuthorizationGrant) -> str:
    return sha256(canonical_json_bytes(grant.model_dump(mode="json"))).hexdigest()


def authorization_grant_approval_sha256(grant: ProtectedAuthorizationGrant) -> str:
    payload = grant.model_dump(
        mode="json",
        exclude={"approval_source_event_id", "approval_source_raw_sha256"},
    )
    return sha256(canonical_json_bytes(payload)).hexdigest()


def new_event_id() -> str:
    return str(uuid4())


__all__ = [
    "ActorIdentity",
    "ApprovalSourceEvidence",
    "AuthorizationAuditAction",
    "AuthorizationAuditEntry",
    "ControlImplementationBinding",
    "OpaqueLogicalRef",
    "OpaqueRefNamespace",
    "OperationAuditEntry",
    "OperationAuditOutcome",
    "ProtectedAction",
    "ProtectedAuditReason",
    "ProtectedApprovalPrincipal",
    "ProtectedApprovalRole",
    "ProtectedAuthorizationCapability",
    "ProtectedAuthorizationGrant",
    "ProtectedDatasetBinding",
    "ProtectedDatasetState",
    "ProtectedOperationRequest",
    "ProtectedOperationResult",
    "ProtectedPrincipal",
    "ProtectedPrincipalRole",
    "ProtectedSecurityError",
    "VerifiedAuthorizationApproval",
    "audit_entry_sha256",
    "authorization_grant_sha256",
    "authorization_grant_approval_sha256",
    "execute_protected_operation",
    "new_event_id",
]
