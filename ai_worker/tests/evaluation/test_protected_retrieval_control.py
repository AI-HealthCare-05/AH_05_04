from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
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
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    audit_entry_sha256,
    authorization_grant_approval_sha256,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import (
    C1ApprovalArtifact,
    ControlCommandKind,
    ControlCommandResult,
    DisableIdentityCommand,
    ExpireAuthorizationCommand,
    FreezeApprovalArtifact,
    FreezeApprovalLocator,
    FreezeApprovalSourceEvidence,
    FreezeDatasetCommand,
    GrantAuthorizationCommand,
    IngestApprovalCommand,
    RegisterDatasetCommand,
    RegisterIdentityCommand,
    RevokeAuthorizationCommand,
    TransitionDatasetCommand,
    c1_approval_artifact_path,
    compute_approval_canonical_raw_sha256,
    control_command_sha256,
    freeze_approval_artifact_path,
    verify_authorization_approval,
    verify_freeze_approval,
)

NOW = datetime(2026, 9, 11, 1, 2, 3, tzinfo=UTC)
REQUEST_ID = "123e4567-e89b-42d3-a456-426614174000"
GRANT_ID = "123e4567-e89b-42d3-a456-426614174001"
DATASET_ID = "123e4567-e89b-42d3-a456-426614174003"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


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


def _dataset_binding(**updates: object) -> ProtectedDatasetBinding:
    payload: dict[str, object] = {
        "dataset_id": DATASET_ID,
        "dataset_version": "1.0.0",
        "manifest_sha256": SHA_A,
        "protected_artifact_sha256": SHA_B,
        "hmac_key_version": "synthetic-key-v1",
        "state": ProtectedDatasetState.ACCESS_AUTHORIZED,
        "state_revision": 1,
        "authored_count": 0,
        "review_complete": False,
        "leakage_axis_intersections": (0, 0, 0, 0),
        "freeze_receipt_ref": None,
    }
    payload.update(updates)
    return ProtectedDatasetBinding.model_validate(payload)


def _register_dataset_payload(**updates: object) -> dict[str, object]:
    binding = _dataset_binding()
    payload: dict[str, object] = {
        "request_id": REQUEST_ID,
        "dataset_id": binding.dataset_id,
        "dataset_version": binding.dataset_version,
        "binding": binding.model_dump(mode="python"),
        "manifest_sha256": binding.manifest_sha256,
        "protected_artifact_sha256": binding.protected_artifact_sha256,
        "hmac_key_version": binding.hmac_key_version,
    }
    payload.update(updates)
    return payload


def _freeze_evidence(**updates: object) -> FreezeApprovalSourceEvidence:
    payload: dict[str, object] = {
        "source_event_id": REQUEST_ID,
        "action": ProtectedAction.FREEZE,
        "dataset_id": DATASET_ID,
        "dataset_version": "1.0.0",
        "manifest_sha256": SHA_A,
        "protected_artifact_sha256": SHA_B,
        "authored_count": 40,
        "review_complete": True,
        "leakage_axis_intersections": (0, 0, 0, 0),
        "issuer": ProtectedApprovalPrincipal(
            actor=ActorIdentity(actor_id="synthetic-reviewer", namespace="GITHUB_LOGIN"),
            role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        ),
        "state": "APPROVED",
        "recorded_at": NOW,
        "target_commit_oid": "0" * 40,
        "target_artifact_sha256": SHA_A,
        "canonical_raw_sha256": SHA_B,
        "implementation_participants": (ActorIdentity(actor_id="participant-1", namespace="GITHUB_LOGIN"),),
    }
    payload.update(updates)
    return FreezeApprovalSourceEvidence.model_validate(payload)


def test_register_dataset_command_requires_exact_initial_binding() -> None:
    binding = _dataset_binding()
    command = RegisterDatasetCommand(
        request_id=REQUEST_ID,
        dataset_id=binding.dataset_id,
        dataset_version=binding.dataset_version,
        binding=binding,
        manifest_sha256=binding.manifest_sha256,
        protected_artifact_sha256=binding.protected_artifact_sha256,
        hmac_key_version=binding.hmac_key_version,
    )
    assert command.binding.state is ProtectedDatasetState.ACCESS_AUTHORIZED
    assert command.binding.state_revision == 1
    assert command.binding.authored_count == 0
    assert command.binding.review_complete is False
    assert command.binding.freeze_receipt_ref is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_id", "not-a-uuid"),
        ("dataset_version", "01.0.0"),
        ("manifest_sha256", "A" * 64),
        ("protected_artifact_sha256", "b" * 63),
    ],
)
def test_dataset_commands_reject_noncanonical_identifiers(field: str, value: str) -> None:
    payload = _register_dataset_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        RegisterDatasetCommand.model_validate(payload)


def test_register_dataset_command_rejects_binding_mismatch_and_noninitial_state() -> None:
    payload = _register_dataset_payload()
    payload["manifest_sha256"] = SHA_B
    with pytest.raises(ValidationError, match="binding"):
        RegisterDatasetCommand.model_validate(payload)
    payload = _register_dataset_payload()
    payload["binding"] = _dataset_binding(state=ProtectedDatasetState.AUTHORING, state_revision=2)
    with pytest.raises(ValidationError, match="initial"):
        RegisterDatasetCommand.model_validate(payload)


def test_freeze_evidence_requires_exact_completed_review_shape() -> None:
    evidence = _freeze_evidence()
    assert evidence.action is ProtectedAction.FREEZE
    assert evidence.authored_count == 40
    assert evidence.review_complete is True
    assert evidence.leakage_axis_intersections == (0, 0, 0, 0)
    assert evidence.issuer.role is ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authored_count", 39),
        ("review_complete", False),
        ("leakage_axis_intersections", (0, 0, 1, 0)),
        ("source_event_id", "not-a-uuid"),
    ],
)
def test_freeze_evidence_rejects_incomplete_or_noncanonical_values(field: str, value: object) -> None:
    payload = _freeze_evidence().model_dump(mode="python")
    payload[field] = value
    with pytest.raises(ValidationError):
        FreezeApprovalSourceEvidence.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "reason", "revision"),
    [
        (ControlCommandKind.REGISTER_DATASET, "DATASET_REGISTERED", 1),
        (ControlCommandKind.TRANSITION_DATASET, "DATASET_TRANSITIONED", 2),
        (ControlCommandKind.FREEZE_DATASET, "DATASET_FROZEN", 3),
    ],
)
def test_dataset_control_results_require_revision_without_authorization_audit(
    kind: ControlCommandKind,
    reason: Any,
    revision: int,
) -> None:
    result = ControlCommandResult(
        request_id=REQUEST_ID,
        command_kind=kind,
        target_id=f"{DATASET_ID}:1.0.0",
        effective_revision=revision,
        authorization_audit_event_id=None,
        reason_code=reason,
    )
    assert result.effective_revision == revision


def test_dataset_control_audit_entries_bind_target_and_reason() -> None:
    entry = ControlCommandAuditEntry(
        event_kind=ProtectedAuditEventKind.CONTROL,
        sequence=1,
        event_id=REQUEST_ID,
        command_kind="REGISTER_DATASET",
        executed_by=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
        target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
        target_id=f"{DATASET_ID}:1.0.0",
        command_sha256=SHA_A,
        outcome=ControlAuditOutcome.SUCCEEDED,
        result_effective_revision=1,
        authorization_audit_event_id=None,
        reason_code=ProtectedAuditReason.DATASET_REGISTERED,
        recorded_at=NOW,
        previous_entry_sha256=None,
        entry_sha256="0" * 64,
    )
    assert entry.command_kind == "REGISTER_DATASET"
    assert entry.target_kind == ControlAuditTargetKind.PROTECTED_DATASET
    assert entry.reason_code == ProtectedAuditReason.DATASET_REGISTERED

    # Denied with FREEZE_EVIDENCE_INCOMPLETE is allowlisted
    denied = ControlCommandAuditEntry(
        event_kind=ProtectedAuditEventKind.CONTROL,
        sequence=1,
        event_id=REQUEST_ID,
        command_kind="FREEZE_DATASET",
        executed_by=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
        target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
        target_id=f"{DATASET_ID}:1.0.0",
        command_sha256=SHA_A,
        outcome=ControlAuditOutcome.DENIED,
        result_effective_revision=None,
        authorization_audit_event_id=None,
        reason_code=ProtectedAuditReason.FREEZE_EVIDENCE_INCOMPLETE,
        recorded_at=NOW,
        previous_entry_sha256=None,
        entry_sha256="0" * 64,
    )
    assert denied.reason_code == ProtectedAuditReason.FREEZE_EVIDENCE_INCOMPLETE

    # Reject invalid target format
    with pytest.raises(ValidationError):
        ControlCommandAuditEntry(
            event_kind=ProtectedAuditEventKind.CONTROL,
            sequence=1,
            event_id=REQUEST_ID,
            command_kind="REGISTER_DATASET",
            executed_by=ActorIdentity(actor_id="synthetic-custodian", namespace="GITHUB_LOGIN"),
            target_kind=ControlAuditTargetKind.PROTECTED_DATASET,
            target_id="invalid-dataset-target",
            command_sha256=SHA_A,
            outcome=ControlAuditOutcome.SUCCEEDED,
            result_effective_revision=1,
            authorization_audit_event_id=None,
            reason_code=ProtectedAuditReason.DATASET_REGISTERED,
            recorded_at=NOW,
            previous_entry_sha256=None,
            entry_sha256="0" * 64,
        )


def test_verify_freeze_approval_matrix() -> None:
    dataset = _dataset_binding(
        state=ProtectedDatasetState.REVIEW_READY,
        state_revision=3,
        authored_count=40,
        review_complete=True,
        leakage_axis_intersections=(0, 0, 0, 0),
    )
    evidence = _freeze_evidence()
    custodian = ProtectedApprovalPrincipal(
        actor=ActorIdentity(actor_id="custodian-actor", namespace="GITHUB_LOGIN"),
        role=ProtectedApprovalRole.DATASET_CUSTODIAN,
    )

    # Success
    verify_freeze_approval(
        dataset,
        evidence,
        approval_source_event_id=evidence.source_event_id,
        expected_raw_sha256=evidence.canonical_raw_sha256,
        executor=custodian,
    )

    # Mismatched source event ID
    with pytest.raises(ProtectedSecurityError) as err:
        verify_freeze_approval(
            dataset,
            evidence,
            approval_source_event_id="00000000-0000-0000-0000-000000000000",
            expected_raw_sha256=evidence.canonical_raw_sha256,
            executor=custodian,
        )
    assert err.value.reason_code == "APPROVAL_EVIDENCE_MISMATCH"

    # Self-approval: issuer == executor
    with pytest.raises(ProtectedSecurityError) as err:
        verify_freeze_approval(
            dataset,
            evidence,
            approval_source_event_id=evidence.source_event_id,
            expected_raw_sha256=evidence.canonical_raw_sha256,
            executor=evidence.issuer,
        )
    assert err.value.reason_code in {"SELF_APPROVAL_DENIED", "ISSUER_ROLE_DENIED"}


def test_transition_and_freeze_dataset_command_shapes() -> None:
    transition_cmd = TransitionDatasetCommand(
        request_id=REQUEST_ID,
        dataset_id=DATASET_ID,
        dataset_version="1.0.0",
        from_state=ProtectedDatasetState.ACCESS_AUTHORIZED,
        to_state=ProtectedDatasetState.AUTHORING,
        expected_state_revision=1,
        authored_count=0,
        review_complete=False,
    )
    assert transition_cmd.from_state is ProtectedDatasetState.ACCESS_AUTHORIZED
    assert transition_cmd.to_state is ProtectedDatasetState.AUTHORING

    freeze_cmd = FreezeDatasetCommand(
        request_id=REQUEST_ID,
        dataset_id=DATASET_ID,
        dataset_version="1.0.0",
        expected_state_revision=3,
        approval_source_event_id=REQUEST_ID,
        expected_raw_sha256=SHA_B,
    )
    assert freeze_cmd.expected_state_revision == 3


def test_freeze_approval_locator_validation() -> None:
    locator = FreezeApprovalLocator(
        source_event_id=REQUEST_ID,
        pull_number=123,
        review_id=456,
    )
    assert locator.source_event_id == REQUEST_ID
    assert locator.pull_number == 123
    assert locator.review_id == 456

    with pytest.raises(ValidationError):
        FreezeApprovalLocator(
            source_event_id="not-a-uuid",
            pull_number=123,
            review_id=456,
        )

    with pytest.raises(ValidationError):
        FreezeApprovalLocator(
            source_event_id=REQUEST_ID,
            pull_number=0,
            review_id=456,
        )

    with pytest.raises(ValidationError):
        FreezeApprovalLocator(
            source_event_id=REQUEST_ID,
            pull_number=123,
            review_id=-1,
        )

    with pytest.raises(ValidationError):
        FreezeApprovalLocator(
            source_event_id=REQUEST_ID,
            pull_number=123,
            review_id=456,
            repository="owner/repo",  # type: ignore[call-arg]
        )


def test_c1_approval_artifact_validation() -> None:
    artifact = C1ApprovalArtifact(
        source_event_id="github:org/repo:pull:123:review:456",
        authorization_action=AuthorizationAuditAction.GRANT,
        approved_grant_payload_sha256=SHA_A,
        issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        target_commit_oid="a" * 40,
        target_artifact_sha256=SHA_B,
        implementation_participants=(
            ActorIdentity(namespace="GITHUB_LOGIN", actor_id="alice"),
            ActorIdentity(namespace="GITHUB_LOGIN", actor_id="bob"),
        ),
    )
    assert artifact.format_id == "c1.authorization-approval-artifact"
    assert artifact.format_version == "1.0.0"

    with pytest.raises(ValidationError):
        C1ApprovalArtifact(
            source_event_id="github:org/repo:pull:123:review:456",
            authorization_action=AuthorizationAuditAction.GRANT,
            approved_grant_payload_sha256=SHA_A,
            issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            target_commit_oid="invalid-oid",
            target_artifact_sha256=SHA_B,
            implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="alice"),),
        )

    with pytest.raises(ValidationError):
        C1ApprovalArtifact(
            source_event_id="github:org/repo:pull:123:review:456",
            authorization_action=AuthorizationAuditAction.GRANT,
            approved_grant_payload_sha256=SHA_A,
            issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            target_commit_oid="a" * 40,
            target_artifact_sha256=SHA_B,
            implementation_participants=(
                ActorIdentity(namespace="GITHUB_LOGIN", actor_id="alice"),
                ActorIdentity(namespace="GITHUB_LOGIN", actor_id="alice"),
            ),
        )

    with pytest.raises(ValidationError):
        C1ApprovalArtifact(
            source_event_id="github:org/repo:pull:123:review:456",
            authorization_action=AuthorizationAuditAction.GRANT,
            approved_grant_payload_sha256=SHA_A,
            issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            target_commit_oid="a" * 40,
            target_artifact_sha256=SHA_B,
            implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="alice"),),
            unexpected_field="disallowed",  # type: ignore[call-arg]
        )


def test_freeze_approval_artifact_validation() -> None:
    artifact = FreezeApprovalArtifact(
        source_event_id=REQUEST_ID,
        dataset_id=DATASET_ID,
        dataset_version="1.0.0",
        manifest_sha256=SHA_A,
        protected_artifact_sha256=SHA_B,
        authored_count=40,
        review_complete=True,
        leakage_axis_intersections=(0, 0, 0, 0),
        issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
        target_commit_oid="b" * 40,
        target_artifact_sha256=SHA_C,
        implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="developer1"),),
    )
    assert artifact.format_id == "freeze.dataset-approval-artifact"
    assert artifact.authored_count == 40
    assert artifact.review_complete is True

    with pytest.raises(ValidationError):
        FreezeApprovalArtifact(
            source_event_id=REQUEST_ID,
            dataset_id=DATASET_ID,
            dataset_version="1.0.0",
            manifest_sha256=SHA_A,
            protected_artifact_sha256=SHA_B,
            authored_count=39,  # type: ignore[arg-type]
            review_complete=True,
            leakage_axis_intersections=(0, 0, 0, 0),
            issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            target_commit_oid="b" * 40,
            target_artifact_sha256=SHA_C,
            implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="developer1"),),
        )

    with pytest.raises(ValidationError):
        FreezeApprovalArtifact(
            source_event_id=REQUEST_ID,
            dataset_id=DATASET_ID,
            dataset_version="1.0.0",
            manifest_sha256=SHA_A,
            protected_artifact_sha256=SHA_B,
            authored_count=40,
            review_complete=False,  # type: ignore[arg-type]
            leakage_axis_intersections=(0, 0, 0, 0),
            issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            target_commit_oid="b" * 40,
            target_artifact_sha256=SHA_C,
            implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="developer1"),),
        )

    with pytest.raises(ValidationError):
        FreezeApprovalArtifact(
            source_event_id=REQUEST_ID,
            dataset_id=DATASET_ID,
            dataset_version="1.0.0",
            manifest_sha256=SHA_A,
            protected_artifact_sha256=SHA_B,
            authored_count=40,
            review_complete=True,
            leakage_axis_intersections=(1, 0, 0, 0),  # type: ignore[arg-type]
            issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            target_commit_oid="b" * 40,
            target_artifact_sha256=SHA_C,
            implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="developer1"),),
        )

    with pytest.raises(ValidationError):
        FreezeApprovalArtifact(
            source_event_id="not-a-uuid",
            dataset_id=DATASET_ID,
            dataset_version="1.0.0",
            manifest_sha256=SHA_A,
            protected_artifact_sha256=SHA_B,
            authored_count=40,
            review_complete=True,
            leakage_axis_intersections=(0, 0, 0, 0),
            issuer_role=ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER,
            target_commit_oid="b" * 40,
            target_artifact_sha256=SHA_C,
            implementation_participants=(ActorIdentity(namespace="GITHUB_LOGIN", actor_id="developer1"),),
        )


def test_compute_approval_canonical_raw_sha256_properties() -> None:
    dt = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
    hash1 = compute_approval_canonical_raw_sha256(
        review_id=100,
        state="APPROVED",
        submitted_at=dt,
        commit_id="a" * 40,
        reviewer_actor_id="reviewer1",
        repository="AI-HealthCare-05/AH_05_04",
        pull_number=772,
        approval_artifact_sha256=SHA_A,
    )
    assert len(hash1) == 64

    # Identical with string format
    hash2 = compute_approval_canonical_raw_sha256(
        review_id=100,
        state="APPROVED",
        submitted_at="2026-09-18T12:00:00Z",
        commit_id="a" * 40,
        reviewer_actor_id="reviewer1",
        repository="AI-HealthCare-05/AH_05_04",
        pull_number=772,
        approval_artifact_sha256=SHA_A,
    )
    assert hash1 == hash2

    # Changing any canonical projection field changes the hash
    hash_diff_commit = compute_approval_canonical_raw_sha256(
        review_id=100,
        state="APPROVED",
        submitted_at=dt,
        commit_id="b" * 40,
        reviewer_actor_id="reviewer1",
        repository="AI-HealthCare-05/AH_05_04",
        pull_number=772,
        approval_artifact_sha256=SHA_A,
    )
    assert hash_diff_commit != hash1

    hash_diff_artifact = compute_approval_canonical_raw_sha256(
        review_id=100,
        state="APPROVED",
        submitted_at=dt,
        commit_id="a" * 40,
        reviewer_actor_id="reviewer1",
        repository="AI-HealthCare-05/AH_05_04",
        pull_number=772,
        approval_artifact_sha256=SHA_B,
    )
    assert hash_diff_artifact != hash1

    hash_diff_repo = compute_approval_canonical_raw_sha256(
        review_id=100,
        state="APPROVED",
        submitted_at=dt,
        commit_id="a" * 40,
        reviewer_actor_id="reviewer1",
        repository="other/repo",
        pull_number=772,
        approval_artifact_sha256=SHA_A,
    )
    assert hash_diff_repo != hash1


def test_exact_artifact_paths() -> None:
    assert c1_approval_artifact_path(772, 9999) == "docs/validation/protected_retrieval/c1/pull_772_review_9999.json"
    assert freeze_approval_artifact_path(REQUEST_ID) == f"docs/validation/protected_retrieval/freeze/{REQUEST_ID}.json"


def test_freeze_command_idempotency_regression_with_locators() -> None:
    freeze_cmd = FreezeDatasetCommand(
        request_id=REQUEST_ID,
        dataset_id=DATASET_ID,
        dataset_version="1.0.0",
        expected_state_revision=3,
        approval_source_event_id=REQUEST_ID,
        expected_raw_sha256=SHA_B,
    )
    # Ensure locator is NOT part of FreezeDatasetCommand
    assert not hasattr(freeze_cmd, "locator")
    assert "locator" not in freeze_cmd.model_dump(mode="json")

    # Command hash is strictly independent of any ephemeral locator
    loc_a = FreezeApprovalLocator(source_event_id=REQUEST_ID, pull_number=1, review_id=100)
    loc_b = FreezeApprovalLocator(source_event_id=REQUEST_ID, pull_number=2, review_id=200)
    assert loc_a != loc_b

    hash_cmd = control_command_sha256(ControlCommandKind.FREEZE_DATASET, freeze_cmd)
    assert len(hash_cmd) == 64
    # Repeated calculation produces identical hash
    assert control_command_sha256(ControlCommandKind.FREEZE_DATASET, freeze_cmd) == hash_cmd
