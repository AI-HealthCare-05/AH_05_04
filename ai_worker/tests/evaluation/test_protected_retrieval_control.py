from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    ControlAuditOutcome,
    ControlAuditTargetKind,
    ControlCommandAuditEntry,
    ControlImplementationBinding,
    ProtectedAction,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationGrant,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    audit_entry_sha256,
    authorization_grant_approval_sha256,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    ControlCommandKind,
    ControlCommandResult,
    DisableIdentityCommand,
    ExpireAuthorizationCommand,
    GrantAuthorizationCommand,
    IngestApprovalCommand,
    RegisterIdentityCommand,
    RevokeAuthorizationCommand,
    control_command_sha256,
    verify_authorization_approval,
)

NOW = datetime(2026, 9, 11, 1, 2, 3, tzinfo=UTC)
REQUEST_ID = "123e4567-e89b-42d3-a456-426614174000"
GRANT_ID = "123e4567-e89b-42d3-a456-426614174001"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _principal(
    actor_id: str = "synthetic-author",
    role: ProtectedPrincipalRole = ProtectedPrincipalRole.HOLDOUT_AUTHOR,
) -> ProtectedPrincipal:
    return ProtectedPrincipal(
        actor=ActorIdentity(actor_id=actor_id, namespace="GITHUB_LOGIN"),
        role=role,
    )


def _issuer(
    actor_id: str = "synthetic-custodian",
    role: ProtectedApprovalRole = ProtectedApprovalRole.DATASET_CUSTODIAN,
) -> ProtectedApprovalPrincipal:
    return ProtectedApprovalPrincipal(
        actor=ActorIdentity(actor_id=actor_id, namespace="GITHUB_LOGIN"),
        role=role,
    )


def _grant(
    *,
    subject: ProtectedPrincipal | None = None,
    issuer: ProtectedApprovalPrincipal | None = None,
    actions: tuple[ProtectedAction, ...] = (ProtectedAction.READ, ProtectedAction.WRITE),
) -> ProtectedAuthorizationGrant:
    return ProtectedAuthorizationGrant(
        grant_id=GRANT_ID,
        revision=1,
        subject=subject or _principal(),
        dataset_id="synthetic-holdout",
        dataset_version="1.0.0",
        manifest_sha256=SHA_A,
        protected_artifact_sha256=SHA_B,
        hmac_key_version="synthetic-key-v1",
        actions=actions,
        issuer=issuer or _issuer(),
        control_implementation=ControlImplementationBinding(
            commit_oid="c" * 40,
            artifact_sha256="d" * 64,
            participants=(ActorIdentity(actor_id="synthetic-implementer", namespace="GITHUB_LOGIN"),),
        ),
        approval_source_event_id="approval-1",
        approval_source_raw_sha256=SHA_A,
        valid_from=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def _evidence(
    grant: ProtectedAuthorizationGrant,
    *,
    action: AuthorizationAuditAction = AuthorizationAuditAction.GRANT,
) -> ApprovalSourceEvidence:
    return ApprovalSourceEvidence(
        source_event_id=grant.approval_source_event_id,
        authorization_action=action,
        approved_grant_payload_sha256=authorization_grant_approval_sha256(grant),
        issuer=grant.issuer,
        state="APPROVED",
        recorded_at=NOW,
        target_commit_oid=grant.control_implementation.commit_oid,
        target_artifact_sha256=grant.control_implementation.artifact_sha256,
        canonical_raw_sha256=grant.approval_source_raw_sha256,
        implementation_participants=grant.control_implementation.participants,
    )


def test_ingest_command_hash_has_a_hand_verified_canonical_value() -> None:
    command = IngestApprovalCommand(
        request_id=REQUEST_ID,
        source_event_id="approval-1",
        expected_raw_sha256=SHA_A,
    )

    assert control_command_sha256(ControlCommandKind.INGEST_APPROVAL, command) == (
        "36dd2d85913cbcceec14abb612bf19c04b7c249d908caf0d9b3ef4bea3a4457c"
    )


def test_command_hash_changes_when_the_expected_source_hash_changes() -> None:
    command = IngestApprovalCommand(
        request_id=REQUEST_ID,
        source_event_id="approval-1",
        expected_raw_sha256=SHA_A,
    )
    changed = command.model_copy(update={"expected_raw_sha256": SHA_B})

    assert control_command_sha256(ControlCommandKind.INGEST_APPROVAL, command) != control_command_sha256(
        ControlCommandKind.INGEST_APPROVAL, changed
    )


@pytest.mark.parametrize(
    "command_type",
    [IngestApprovalCommand, GrantAuthorizationCommand, RevokeAuthorizationCommand, ExpireAuthorizationCommand],
)
def test_control_commands_reject_non_v4_request_ids(command_type: type[object]) -> None:
    grant = _grant()
    values: dict[str, object] = {
        "request_id": str(UUID(int=0, version=1)),
        "source_event_id": "approval-1",
        "expected_raw_sha256": SHA_A,
        "grant": grant,
        "expected_dataset_state_revision": 1,
        "grant_id": grant.grant_id,
        "approval_source_event_id": "approval-revoke-1",
        "expected_effective_revision": 1,
    }
    fields = command_type.model_fields  # type: ignore[attr-defined]

    with pytest.raises(ValidationError):
        command_type(**{name: values[name] for name in fields})  # type: ignore[operator]


def test_control_commands_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        IngestApprovalCommand(
            request_id=REQUEST_ID,
            source_event_id="approval-1",
            expected_raw_sha256=SHA_A,
            evidence_body="must-not-cross-the-boundary",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize("command_type", [RevokeAuthorizationCommand, ExpireAuthorizationCommand])
def test_grant_commands_reject_non_v4_grant_ids(command_type: type[object]) -> None:
    values: dict[str, object] = {
        "request_id": REQUEST_ID,
        "grant_id": str(UUID(int=1, version=1)),
        "approval_source_event_id": "approval-revoke-1",
        "expected_raw_sha256": SHA_A,
        "expected_effective_revision": 1,
    }
    fields = command_type.model_fields  # type: ignore[attr-defined]

    with pytest.raises(ValidationError):
        command_type(**{name: values[name] for name in fields})  # type: ignore[operator]


def test_control_result_rejects_a_non_v4_request_id() -> None:
    with pytest.raises(ValidationError):
        ControlCommandResult(
            request_id=str(UUID(int=2, version=1)),
            command_kind=ControlCommandKind.GRANT,
            target_id=GRANT_ID,
            effective_revision=1,
            authorization_audit_event_id="123e4567-e89b-42d3-a456-426614174002",
            reason_code="AUTHORIZED",
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"reason_code": "EXPIRED"},
        {"effective_revision": None},
        {"authorization_audit_event_id": None},
        {"authorization_audit_event_id": "not-a-uuid"},
        {"target_id": "not-a-uuid"},
    ],
)
def test_control_result_rejects_command_result_mismatches(updates: dict[str, object]) -> None:
    values: dict[str, object] = {
        "request_id": REQUEST_ID,
        "command_kind": ControlCommandKind.GRANT,
        "target_id": GRANT_ID,
        "effective_revision": 1,
        "authorization_audit_event_id": "123e4567-e89b-42d3-a456-426614174002",
        "reason_code": "AUTHORIZED",
    }
    values.update(updates)

    with pytest.raises(ValidationError):
        ControlCommandResult(**values)  # type: ignore[arg-type]


def test_verified_approval_binds_the_complete_grant() -> None:
    grant = _grant()
    evidence = _evidence(grant)

    verified = verify_authorization_approval(
        grant,
        evidence,
        AuthorizationAuditAction.GRANT,
        grant.approval_source_raw_sha256,
    )

    assert verified.is_verified()
    assert verified.evidence == evidence
    assert verified.action is AuthorizationAuditAction.GRANT


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda item: item.model_copy(update={"authorization_action": AuthorizationAuditAction.REVOKE}),
            "APPROVAL_ACTION_MISMATCH",
        ),
        (lambda item: item.model_copy(update={"canonical_raw_sha256": SHA_B}), "APPROVAL_EVIDENCE_MISMATCH"),
        (
            lambda item: item.model_copy(update={"approved_grant_payload_sha256": SHA_B}),
            "APPROVAL_GRANT_BINDING_MISMATCH",
        ),
    ],
)
def test_verified_approval_rejects_source_binding_mutations(mutation: object, reason: str) -> None:
    grant = _grant()
    evidence = mutation(_evidence(grant))  # type: ignore[operator]

    with pytest.raises(ProtectedSecurityError, match=reason):
        verify_authorization_approval(
            grant,
            evidence,
            AuthorizationAuditAction.GRANT,
            grant.approval_source_raw_sha256,
        )


def test_verified_approval_rejects_an_implementation_participant_as_issuer() -> None:
    grant = _grant(issuer=_issuer("synthetic-implementer"))

    with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
        verify_authorization_approval(
            grant,
            _evidence(grant),
            AuthorizationAuditAction.GRANT,
            grant.approval_source_raw_sha256,
        )


def test_product_safety_reviewer_can_issue_only_a_custodian_grant() -> None:
    reviewer = _issuer("synthetic-reviewer", ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER)
    author_grant = _grant(issuer=reviewer)
    custodian_grant = _grant(
        subject=_principal("synthetic-custodian-subject", ProtectedPrincipalRole.DATASET_CUSTODIAN),
        issuer=reviewer,
        actions=(ProtectedAction.READ, ProtectedAction.FREEZE),
    )

    with pytest.raises(ProtectedSecurityError, match="ISSUER_ROLE_DENIED"):
        verify_authorization_approval(
            author_grant,
            _evidence(author_grant),
            AuthorizationAuditAction.GRANT,
            author_grant.approval_source_raw_sha256,
        )

    assert verify_authorization_approval(
        custodian_grant,
        _evidence(custodian_grant),
        AuthorizationAuditAction.GRANT,
        custodian_grant.approval_source_raw_sha256,
    ).is_verified()


def test_control_audit_hash_covers_the_command_outcome() -> None:
    entry = ControlCommandAuditEntry(
        event_kind=ProtectedAuditEventKind.CONTROL,
        sequence=1,
        event_id=REQUEST_ID,
        command_kind="GRANT",
        executed_by=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
        target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
        target_id=GRANT_ID,
        command_sha256=SHA_A,
        outcome=ControlAuditOutcome.SUCCEEDED,
        result_effective_revision=1,
        authorization_audit_event_id="123e4567-e89b-42d3-a456-426614174002",
        reason_code=ProtectedAuditReason.AUTHORIZED,
        recorded_at=NOW,
        previous_entry_sha256=None,
        entry_sha256="0" * 64,
    )
    digest = audit_entry_sha256(entry)

    denied = entry.model_copy(
        update={
            "outcome": ControlAuditOutcome.DENIED,
            "result_effective_revision": None,
            "authorization_audit_event_id": None,
            "reason_code": ProtectedAuditReason.ISSUER_ROLE_DENIED,
        }
    )

    assert digest != audit_entry_sha256(denied)


@pytest.mark.parametrize(
    "updates",
    [
        {"target_kind": ControlAuditTargetKind.APPROVAL_SOURCE_EVENT},
        {"reason_code": ProtectedAuditReason.EXPIRED},
        {"result_effective_revision": None},
        {"authorization_audit_event_id": None},
        {"authorization_audit_event_id": "not-a-uuid"},
    ],
)
def test_control_audit_rejects_semantic_mismatches(updates: dict[str, object]) -> None:
    values: dict[str, object] = {
        "event_kind": ProtectedAuditEventKind.CONTROL,
        "sequence": 1,
        "event_id": REQUEST_ID,
        "command_kind": "GRANT",
        "executed_by": ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
        "target_kind": ControlAuditTargetKind.AUTHORIZATION_GRANT,
        "target_id": GRANT_ID,
        "command_sha256": SHA_A,
        "outcome": ControlAuditOutcome.SUCCEEDED,
        "result_effective_revision": 1,
        "authorization_audit_event_id": "123e4567-e89b-42d3-a456-426614174002",
        "reason_code": ProtectedAuditReason.AUTHORIZED,
        "recorded_at": NOW,
        "previous_entry_sha256": None,
        "entry_sha256": "0" * 64,
    }
    values.update(updates)

    with pytest.raises(ValidationError):
        ControlCommandAuditEntry(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("reason", [ProtectedAuditReason.AUTHORIZED, ProtectedAuditReason.COMPLETED])
def test_denied_control_audit_rejects_a_non_control_denial_reason(reason: ProtectedAuditReason) -> None:
    with pytest.raises(ValidationError):
        ControlCommandAuditEntry(
            event_kind=ProtectedAuditEventKind.CONTROL,
            sequence=1,
            event_id=REQUEST_ID,
            command_kind="GRANT",
            executed_by=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
            target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
            target_id=GRANT_ID,
            command_sha256=SHA_A,
            outcome=ControlAuditOutcome.DENIED,
            result_effective_revision=None,
            authorization_audit_event_id=None,
            reason_code=reason,
            recorded_at=NOW,
            previous_entry_sha256=None,
            entry_sha256="0" * 64,
        )


def test_register_identity_command_validates_data_and_control_planes() -> None:
    data_cmd = RegisterIdentityCommand(
        request_id=REQUEST_ID,
        database_login="test_data_user",
        actor_id="test-data-actor",
        actor_namespace="SERVICE_IDENTITY",
        identity_plane="DATA",
        principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
    )
    assert data_cmd.identity_plane == "DATA"
    assert data_cmd.principal_role == ProtectedPrincipalRole.HOLDOUT_AUTHOR
    assert data_cmd.approval_role is None
    assert data_cmd.enabled is True

    control_cmd = RegisterIdentityCommand(
        request_id=REQUEST_ID,
        database_login="test_control_user",
        actor_id="test-control-actor",
        actor_namespace="GITHUB_LOGIN",
        identity_plane="CONTROL",
        approval_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
    )
    assert control_cmd.identity_plane == "CONTROL"
    assert control_cmd.approval_role == ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER
    assert control_cmd.principal_role is None

    # DATA plane requires principal_role, forbids approval_role
    with pytest.raises(ValidationError, match="requires principal_role"):
        RegisterIdentityCommand(
            request_id=REQUEST_ID,
            database_login="test_user",
            actor_id="test-actor",
            actor_namespace="SERVICE_IDENTITY",
            identity_plane="DATA",
            principal_role=None,
        )
    with pytest.raises(ValidationError, match="forbids approval_role"):
        RegisterIdentityCommand(
            request_id=REQUEST_ID,
            database_login="test_user",
            actor_id="test-actor",
            actor_namespace="SERVICE_IDENTITY",
            identity_plane="DATA",
            principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
            approval_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        )

    # CONTROL plane requires approval_role, forbids principal_role
    with pytest.raises(ValidationError, match="requires approval_role"):
        RegisterIdentityCommand(
            request_id=REQUEST_ID,
            database_login="test_user",
            actor_id="test-actor",
            actor_namespace="GITHUB_LOGIN",
            identity_plane="CONTROL",
            approval_role=None,
        )
    with pytest.raises(ValidationError, match="forbids principal_role"):
        RegisterIdentityCommand(
            request_id=REQUEST_ID,
            database_login="test_user",
            actor_id="test-actor",
            actor_namespace="GITHUB_LOGIN",
            identity_plane="CONTROL",
            principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
            approval_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        )

    # Invalid database_login (not PostgreSQL identifier)
    with pytest.raises(ValidationError):
        RegisterIdentityCommand(
            request_id=REQUEST_ID,
            database_login="invalid-login-with-dashes",
            actor_id="test-actor",
            actor_namespace="SERVICE_IDENTITY",
            identity_plane="DATA",
            principal_role=ProtectedPrincipalRole.HOLDOUT_AUTHOR,
        )


def test_disable_identity_command_validates_login_and_actor() -> None:
    cmd = DisableIdentityCommand(
        request_id=REQUEST_ID,
        database_login="valid_login_1",
        expected_actor_id="valid-actor",
        expected_actor_namespace="GITHUB_LOGIN",
    )
    assert cmd.database_login == "valid_login_1"
    assert cmd.expected_actor_id == "valid-actor"

    with pytest.raises(ValidationError):
        DisableIdentityCommand(
            request_id=REQUEST_ID,
            database_login="invalid-login",
            expected_actor_id="valid-actor",
            expected_actor_namespace="GITHUB_LOGIN",
        )


def test_control_command_result_for_identity_commands() -> None:
    reg_result = ControlCommandResult(
        request_id=REQUEST_ID,
        command_kind=ControlCommandKind.REGISTER_IDENTITY,
        target_id="test_login",
        effective_revision=None,
        authorization_audit_event_id=None,
        reason_code="IDENTITY_REGISTERED",
    )
    assert reg_result.target_id == "test_login"
    assert reg_result.reason_code == "IDENTITY_REGISTERED"

    dis_result = ControlCommandResult(
        request_id=REQUEST_ID,
        command_kind=ControlCommandKind.DISABLE_IDENTITY,
        target_id="test_login",
        effective_revision=None,
        authorization_audit_event_id=None,
        reason_code="IDENTITY_DISABLED",
    )
    assert dis_result.target_id == "test_login"
    assert dis_result.reason_code == "IDENTITY_DISABLED"

    # Identity command result rejects revision and authorization audit ref
    with pytest.raises(ValidationError):
        ControlCommandResult(
            request_id=REQUEST_ID,
            command_kind=ControlCommandKind.REGISTER_IDENTITY,
            target_id="test_login",
            effective_revision=1,
            authorization_audit_event_id=None,
            reason_code="IDENTITY_REGISTERED",
        )
    with pytest.raises(ValidationError):
        ControlCommandResult(
            request_id=REQUEST_ID,
            command_kind=ControlCommandKind.DISABLE_IDENTITY,
            target_id="test_login",
            effective_revision=None,
            authorization_audit_event_id=REQUEST_ID,
            reason_code="IDENTITY_DISABLED",
        )


def test_control_audit_entry_for_identity_commands() -> None:
    entry = ControlCommandAuditEntry(
        event_kind=ProtectedAuditEventKind.CONTROL,
        sequence=1,
        event_id=REQUEST_ID,
        command_kind="REGISTER_IDENTITY",
        executed_by=ActorIdentity(actor_id="synthetic-reviewer", namespace="GITHUB_LOGIN"),
        target_kind=ControlAuditTargetKind.PROTECTED_IDENTITY,
        target_id="test_user",
        command_sha256=SHA_A,
        outcome=ControlAuditOutcome.SUCCEEDED,
        result_effective_revision=None,
        authorization_audit_event_id=None,
        reason_code=ProtectedAuditReason.IDENTITY_REGISTERED,
        recorded_at=NOW,
        previous_entry_sha256=None,
        entry_sha256="0" * 64,
    )
    assert entry.command_kind == "REGISTER_IDENTITY"
    assert entry.target_kind == ControlAuditTargetKind.PROTECTED_IDENTITY
    assert entry.reason_code == ProtectedAuditReason.IDENTITY_REGISTERED

    # Denial audit for identity command
    denied = ControlCommandAuditEntry(
        event_kind=ProtectedAuditEventKind.CONTROL,
        sequence=1,
        event_id=REQUEST_ID,
        command_kind="DISABLE_IDENTITY",
        executed_by=ActorIdentity(actor_id="synthetic-reviewer", namespace="GITHUB_LOGIN"),
        target_kind=ControlAuditTargetKind.PROTECTED_IDENTITY,
        target_id="test_user",
        command_sha256=SHA_A,
        outcome=ControlAuditOutcome.DENIED,
        result_effective_revision=None,
        authorization_audit_event_id=None,
        reason_code=ProtectedAuditReason.SELF_APPROVAL_DENIED,
        recorded_at=NOW,
        previous_entry_sha256=None,
        entry_sha256="0" * 64,
    )
    assert denied.reason_code == ProtectedAuditReason.SELF_APPROVAL_DENIED

    # Reject invalid target_kind for identity command
    with pytest.raises(ValidationError):
        ControlCommandAuditEntry(
            event_kind=ProtectedAuditEventKind.CONTROL,
            sequence=1,
            event_id=REQUEST_ID,
            command_kind="REGISTER_IDENTITY",
            executed_by=ActorIdentity(actor_id="synthetic-reviewer", namespace="GITHUB_LOGIN"),
            target_kind=ControlAuditTargetKind.AUTHORIZATION_GRANT,
            target_id="test_user",
            command_sha256=SHA_A,
            outcome=ControlAuditOutcome.SUCCEEDED,
            result_effective_revision=None,
            authorization_audit_event_id=None,
            reason_code=ProtectedAuditReason.IDENTITY_REGISTERED,
            recorded_at=NOW,
            previous_entry_sha256=None,
            entry_sha256="0" * 64,
        )


def test_identity_command_sha256_is_deterministic() -> None:
    cmd1 = RegisterIdentityCommand(
        request_id=REQUEST_ID,
        database_login="test_user_a",
        actor_id="actor-a",
        actor_namespace="GITHUB_LOGIN",
        identity_plane="CONTROL",
        approval_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
    )
    cmd2 = RegisterIdentityCommand(
        request_id=REQUEST_ID,
        database_login="test_user_a",
        actor_id="actor-a",
        actor_namespace="GITHUB_LOGIN",
        identity_plane="CONTROL",
        approval_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
    )
    cmd3 = RegisterIdentityCommand(
        request_id=REQUEST_ID,
        database_login="test_user_b",
        actor_id="actor-a",
        actor_namespace="GITHUB_LOGIN",
        identity_plane="CONTROL",
        approval_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
    )
    assert control_command_sha256(ControlCommandKind.REGISTER_IDENTITY, cmd1) == control_command_sha256(
        ControlCommandKind.REGISTER_IDENTITY, cmd2
    )
    assert control_command_sha256(ControlCommandKind.REGISTER_IDENTITY, cmd1) != control_command_sha256(
        ControlCommandKind.REGISTER_IDENTITY, cmd3
    )


def test_register_identity_command_rejects_enabled_false() -> None:
    with pytest.raises(ValidationError):
        RegisterIdentityCommand(
            request_id=REQUEST_ID,
            database_login="test_user",
            actor_id="synthetic-reviewer",
            actor_namespace="GITHUB_LOGIN",
            identity_plane="CONTROL",
            approval_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            enabled=False,  # type: ignore[arg-type]
        )


def test_identity_command_result_and_audit_reject_invalid_target_id() -> None:
    # Result rejects invalid database_login as target_id
    with pytest.raises(ValidationError):
        ControlCommandResult(
            request_id=REQUEST_ID,
            command_kind=ControlCommandKind.REGISTER_IDENTITY,
            target_id="123-invalid-ident!",
            effective_revision=None,
            authorization_audit_event_id=None,
            reason_code="IDENTITY_REGISTERED",
        )

    # Audit rejects invalid database_login as target_id
    with pytest.raises(ValidationError):
        ControlCommandAuditEntry(
            event_kind=ProtectedAuditEventKind.CONTROL,
            sequence=1,
            event_id=REQUEST_ID,
            command_kind="REGISTER_IDENTITY",
            executed_by=ActorIdentity(actor_id="synthetic-reviewer", namespace="GITHUB_LOGIN"),
            target_kind=ControlAuditTargetKind.PROTECTED_IDENTITY,
            target_id="123-invalid-ident!",
            command_sha256=SHA_A,
            outcome=ControlAuditOutcome.SUCCEEDED,
            result_effective_revision=None,
            authorization_audit_event_id=None,
            reason_code=ProtectedAuditReason.IDENTITY_REGISTERED,
            recorded_at=NOW,
            previous_entry_sha256=None,
            entry_sha256="0" * 64,
        )
