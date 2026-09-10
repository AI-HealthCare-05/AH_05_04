from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.protected_retrieval import (
    ActorIdentity,
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    AuthorizationAuditEntry,
    ControlImplementationBinding,
    OpaqueLogicalRef,
    OpaqueRefNamespace,
    OperationAuditOutcome,
    ProtectedAction,
    ProtectedApprovalPrincipal,
    ProtectedApprovalRole,
    ProtectedAuditReason,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedOperationRequest,
    ProtectedOperationResult,
    ProtectedPrincipal,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    authorization_grant_approval_sha256,
    execute_protected_operation,
)
from ai_worker.tasks.evaluation.protected_retrieval_synthetic import (
    FixedTrustedClock,
    InMemoryApprovalEvidenceVerifier,
    InMemoryAuthorizationGuard,
    InMemoryAuthorizationLedger,
    InMemoryProtectedAuditJournal,
    SyntheticProtectedOperation,
)

NOW = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
COMMIT = "c" * 40


def _ref(namespace: OpaqueRefNamespace) -> OpaqueLogicalRef:
    return OpaqueLogicalRef(namespace=namespace, value=str(uuid4()))


def _principal(
    actor_id: str = "holdout-author",
    role: ProtectedPrincipalRole = ProtectedPrincipalRole.HOLDOUT_AUTHOR,
) -> ProtectedPrincipal:
    return ProtectedPrincipal(actor=ActorIdentity(actor_id=actor_id, namespace="GITHUB_LOGIN"), role=role)


def _approval_principal(
    actor_id: str = "phina-io",
    role: ProtectedApprovalRole = ProtectedApprovalRole.DATASET_CUSTODIAN,
) -> ProtectedApprovalPrincipal:
    return ProtectedApprovalPrincipal(actor=ActorIdentity(actor_id=actor_id, namespace="GITHUB_LOGIN"), role=role)


def _control_binding() -> ControlImplementationBinding:
    return ControlImplementationBinding(
        commit_oid=COMMIT,
        artifact_sha256=SHA_A,
        participants=(ActorIdentity(actor_id="ceohwj", namespace="GITHUB_LOGIN"),),
    )


def _dataset(
    *,
    dataset_version: str = "1.0.0",
    state: ProtectedDatasetState = ProtectedDatasetState.AUTHORING,
    state_revision: int = 3,
    artifact_sha256: str = SHA_B,
    authored_count: int = 0,
    review_complete: bool = False,
    leakage_axis_intersections: tuple[int, int, int, int] | None = None,
    freeze_receipt_ref: OpaqueLogicalRef | None = None,
    execution_authorization_ref: OpaqueLogicalRef | None = None,
    retriever_binding_ref: OpaqueLogicalRef | None = None,
) -> ProtectedDatasetBinding:
    return ProtectedDatasetBinding(
        dataset_id="rag-natural-language-retrieval-holdout",
        dataset_version=dataset_version,
        manifest_sha256=SHA_A,
        protected_artifact_sha256=artifact_sha256,
        hmac_key_version="holdout-key-v1",
        state=state,
        state_revision=state_revision,
        authored_count=authored_count,
        review_complete=review_complete,
        leakage_axis_intersections=leakage_axis_intersections,
        freeze_receipt_ref=freeze_receipt_ref,
        execution_authorization_ref=execution_authorization_ref,
        retriever_binding_ref=retriever_binding_ref,
    )


def _approval_source(
    grant: ProtectedAuthorizationGrant,
    *,
    issuer: ProtectedApprovalPrincipal | None = None,
    participants: tuple[ActorIdentity, ...] | None = None,
    action: AuthorizationAuditAction = AuthorizationAuditAction.GRANT,
    source_event_id: str = "github-pr-review-123",
) -> ApprovalSourceEvidence:
    binding = _control_binding()
    return ApprovalSourceEvidence(
        source_event_id=source_event_id,
        authorization_action=action,
        approved_grant_payload_sha256=authorization_grant_approval_sha256(grant),
        issuer=issuer or _approval_principal(),
        state="APPROVED",
        recorded_at=NOW,
        target_commit_oid=binding.commit_oid,
        target_artifact_sha256=binding.artifact_sha256,
        canonical_raw_sha256=SHA_B,
        implementation_participants=participants or binding.participants,
    )


def _grant(
    *,
    subject: ProtectedPrincipal | None = None,
    issuer: ProtectedApprovalPrincipal | None = None,
    dataset: ProtectedDatasetBinding | None = None,
    actions: tuple[ProtectedAction, ...] = (ProtectedAction.WRITE,),
) -> ProtectedAuthorizationGrant:
    dataset = dataset or _dataset()
    return ProtectedAuthorizationGrant(
        grant_id=str(uuid4()),
        revision=1,
        subject=subject or _principal(),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        manifest_sha256=dataset.manifest_sha256,
        protected_artifact_sha256=dataset.protected_artifact_sha256,
        hmac_key_version=dataset.hmac_key_version,
        actions=actions,
        issuer=issuer or _approval_principal(),
        control_implementation=_control_binding(),
        approval_source_event_id="github-pr-review-123",
        approval_source_raw_sha256=SHA_B,
        valid_from=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )


def _request(
    *,
    principal: ProtectedPrincipal | None = None,
    dataset: ProtectedDatasetBinding | None = None,
    action: ProtectedAction = ProtectedAction.WRITE,
    operation_key: str = "write-origin-01",
) -> ProtectedOperationRequest:
    return ProtectedOperationRequest(
        request_id=str(uuid4()),
        operation_key=operation_key,
        dataset=dataset or _dataset(),
        target_ref=_ref(OpaqueRefNamespace.HOLDOUT_SET),
        action=action,
        principal=principal or _principal(),
    )


def _result() -> ProtectedOperationResult:
    return ProtectedOperationResult(
        result_ref=_ref(OpaqueRefNamespace.RUN_RESULT),
        reason_code="PROTECTED_OPERATION_SUCCEEDED",
    )


def _authorized_components(
    grant: ProtectedAuthorizationGrant,
    source: ApprovalSourceEvidence,
    *,
    dataset: ProtectedDatasetBinding | None = None,
) -> tuple[
    FixedTrustedClock,
    InMemoryProtectedAuditJournal,
    InMemoryAuthorizationLedger,
    InMemoryAuthorizationGuard,
    SyntheticProtectedOperation,
]:
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    verifier = InMemoryApprovalEvidenceVerifier({source.source_event_id: source})
    ledger = InMemoryAuthorizationLedger(journal, clock, verifier)
    ledger.register_dataset(dataset or _dataset())
    ledger._grant(grant)
    return clock, journal, ledger, InMemoryAuthorizationGuard(ledger, clock), SyntheticProtectedOperation()


def test_opaque_logical_ref_requires_a_random_uuid_v4() -> None:
    with pytest.raises(ValidationError):
        OpaqueLogicalRef(namespace=OpaqueRefNamespace.HOLDOUT_SET, value=SHA_A)

    first = _ref(OpaqueRefNamespace.HOLDOUT_SET)
    second = _ref(OpaqueRefNamespace.HOLDOUT_SET)

    assert first.value != second.value


@pytest.mark.parametrize(
    "mutation",
    [
        lambda source: source.model_copy(update={"state": "PENDING"}),
        lambda source: source.model_copy(update={"target_commit_oid": "d" * 40}),
        lambda source: source.model_copy(update={"target_artifact_sha256": "d" * 64}),
        lambda source: source.model_copy(update={"canonical_raw_sha256": "d" * 64}),
        lambda source: source.model_copy(update={"implementation_participants": ()}),
    ],
)
def test_approval_verifier_rejects_forged_or_incomplete_source(mutation: object) -> None:
    grant = _grant()
    trusted = _approval_source(grant)
    presented = mutation(trusted)  # type: ignore[operator]
    verifier = InMemoryApprovalEvidenceVerifier({trusted.source_event_id: trusted})

    with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH"):
        verifier.verify(grant, presented=presented)


@pytest.mark.asyncio
async def test_ledger_rejects_an_unverified_approval_and_audit_failure_is_atomic() -> None:
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    grant = _grant()
    ledger = InMemoryAuthorizationLedger(journal, clock, InMemoryApprovalEvidenceVerifier({}))

    with pytest.raises(ProtectedSecurityError, match="APPROVAL_EVIDENCE_MISMATCH"):
        await ledger.grant(grant)
    assert ledger.current(grant.grant_id) is None

    source = _approval_source(grant)
    ledger = InMemoryAuthorizationLedger(
        journal,
        clock,
        InMemoryApprovalEvidenceVerifier({grant.approval_source_event_id: source}),
    )
    journal.fail_next_append = True
    with pytest.raises(ProtectedSecurityError, match="AUDIT_UNAVAILABLE"):
        await ledger.grant(grant)
    assert ledger.current(grant.grant_id) is None


@pytest.mark.asyncio
async def test_execute_protected_operation_awaits_infrastructure_protocols() -> None:
    grant = _grant()
    source = _approval_source(grant)
    clock, journal, ledger, guard, operation = _authorized_components(grant, source)
    history = journal.operation_history
    append = journal.append_operation
    require_dataset = ledger.require_dataset
    journal.operation_history = AsyncMock(side_effect=history)  # type: ignore[method-assign]
    journal.append_operation = AsyncMock(side_effect=append)  # type: ignore[method-assign]
    ledger.require_dataset = AsyncMock(side_effect=require_dataset)  # type: ignore[method-assign]

    result = await execute_protected_operation(
        _request(),
        ledger=ledger,
        guard=guard,
        journal=journal,
        operation=operation,
        clock=clock,
    )

    assert result.reason_code == "PROTECTED_OPERATION_SUCCEEDED"
    assert journal.operation_history.await_count >= 2
    assert journal.append_operation.await_count == 2
    assert ledger.require_dataset.await_count >= 1


@pytest.mark.asyncio
async def test_verified_approval_cannot_be_reused_for_a_different_grant() -> None:
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    grant = _grant()
    source = _approval_source(grant)
    verifier = InMemoryApprovalEvidenceVerifier({grant.approval_source_event_id: source})
    ledger = InMemoryAuthorizationLedger(journal, clock, verifier)
    different_grant = grant.model_copy(
        update={
            "grant_id": str(uuid4()),
            "subject": _principal("different-author"),
        }
    )

    with pytest.raises(ProtectedSecurityError, match="APPROVAL_GRANT_BINDING_MISMATCH"):
        await ledger.grant(different_grant)

    assert ledger.current(different_grant.grant_id) is None


def test_same_approval_source_cannot_verify_a_different_grant() -> None:
    original = _grant()
    trusted = _approval_source(original)
    verifier = InMemoryApprovalEvidenceVerifier({trusted.source_event_id: trusted})
    verifier.verify(original)
    different = original.model_copy(update={"grant_id": str(uuid4()), "subject": _principal("unapproved-runner")})

    with pytest.raises(ProtectedSecurityError, match="APPROVAL_GRANT_BINDING_MISMATCH"):
        verifier.verify(different)


def test_approval_digest_is_constructible_before_source_provenance_is_added() -> None:
    draft = _grant().model_copy(update={"approval_source_event_id": "pending", "approval_source_raw_sha256": "0" * 64})
    approved_payload_sha256 = authorization_grant_approval_sha256(draft)
    raw_source = canonical_json_bytes(
        {
            "action": "GRANT",
            "approved_grant_payload_sha256": approved_payload_sha256,
            "event_id": "github-pr-review-real-bytes",
            "issuer": draft.issuer.model_dump(mode="json"),
        }
    )
    source_raw_sha256 = sha256(raw_source).hexdigest()
    grant = draft.model_copy(
        update={
            "approval_source_event_id": "github-pr-review-real-bytes",
            "approval_source_raw_sha256": source_raw_sha256,
        }
    )
    source = _approval_source(grant, source_event_id="github-pr-review-real-bytes").model_copy(
        update={"canonical_raw_sha256": source_raw_sha256}
    )

    assert authorization_grant_approval_sha256(grant) == approved_payload_sha256
    InMemoryApprovalEvidenceVerifier({source.source_event_id: source}).verify(grant)


def test_ledger_does_not_accept_a_caller_created_verified_approval() -> None:
    grant = _grant()
    source = _approval_source(grant)
    forged = type(InMemoryApprovalEvidenceVerifier({source.source_event_id: source}).verify(grant))._from_verified(
        source, grant, AuthorizationAuditAction.GRANT
    )
    clock = FixedTrustedClock(NOW)
    ledger = InMemoryAuthorizationLedger(
        InMemoryProtectedAuditJournal(clock), clock, InMemoryApprovalEvidenceVerifier({})
    )

    with pytest.raises(TypeError):
        cast(Callable[..., None], ledger.grant)(grant, forged)


@pytest.mark.asyncio
async def test_revoke_requires_a_distinct_verified_revoke_event_and_is_audit_atomic() -> None:
    grant = _grant()
    grant_source = _approval_source(grant)
    revoke_source_raw_sha256 = "c" * 64
    revoke_source = _approval_source(
        grant,
        action=AuthorizationAuditAction.REVOKE,
        source_event_id="github-pr-review-revoke-456",
    ).model_copy(update={"canonical_raw_sha256": revoke_source_raw_sha256})
    verifier = InMemoryApprovalEvidenceVerifier(
        {
            grant_source.source_event_id: grant_source,
            revoke_source.source_event_id: revoke_source,
        }
    )
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    ledger = InMemoryAuthorizationLedger(journal, clock, verifier)
    await ledger.grant(grant)

    with pytest.raises(ProtectedSecurityError, match="APPROVAL_ACTION_MISMATCH"):
        await ledger.revoke(grant.grant_id, grant_source.source_event_id, SHA_B)
    assert ledger.current(grant.grant_id) == grant

    journal.fail_next_append = True
    with pytest.raises(ProtectedSecurityError, match="AUDIT_UNAVAILABLE"):
        await ledger.revoke(grant.grant_id, revoke_source.source_event_id, revoke_source_raw_sha256)
    assert ledger.current(grant.grant_id) == grant

    await ledger.revoke(grant.grant_id, revoke_source.source_event_id, revoke_source_raw_sha256)
    assert ledger.current(grant.grant_id) is None
    assert ledger.current_revision(grant.grant_id) == grant.revision + 1
    entry = journal.entries[-1]
    assert isinstance(entry, AuthorizationAuditEntry)
    assert entry.action is AuthorizationAuditAction.REVOKE
    assert entry.effective_revision == grant.revision + 1
    assert entry.approval_source_raw_sha256 == revoke_source_raw_sha256


def test_approval_verifier_rejects_subject_or_implementation_participant_as_issuer() -> None:
    self_issuer = _approval_principal("holdout-author")
    self_grant = _grant(issuer=self_issuer)
    with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
        InMemoryApprovalEvidenceVerifier(
            {self_grant.approval_source_event_id: _approval_source(self_grant, issuer=self_issuer)}
        ).verify(self_grant)

    participant_issuer = _approval_principal("ceohwj")
    participant_grant = _grant(issuer=participant_issuer)
    with pytest.raises(ProtectedSecurityError, match="SELF_APPROVAL_DENIED"):
        InMemoryApprovalEvidenceVerifier(
            {participant_grant.approval_source_event_id: _approval_source(participant_grant, issuer=participant_issuer)}
        ).verify(participant_grant)


def test_product_safety_reviewer_cannot_issue_runner_grants() -> None:
    runner = _principal("runner", ProtectedPrincipalRole.PROTECTED_RUNNER)
    safety = _approval_principal("hazelnutflavoured", ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER)
    grant = _grant(subject=runner, issuer=safety, actions=(ProtectedAction.RUN,))

    with pytest.raises(ProtectedSecurityError, match="ISSUER_ROLE_DENIED"):
        InMemoryApprovalEvidenceVerifier(
            {grant.approval_source_event_id: _approval_source(grant, issuer=safety)}
        ).verify(grant)


@pytest.mark.parametrize(
    ("role", "action"),
    [
        (ProtectedPrincipalRole.HOLDOUT_AUTHOR, ProtectedAction.FREEZE),
        (ProtectedPrincipalRole.PROTECTED_RUNNER, ProtectedAction.WRITE),
        (ProtectedPrincipalRole.DATASET_CUSTODIAN, ProtectedAction.RUN),
    ],
)
def test_grant_issuance_rejects_actions_outside_the_subject_role(
    role: ProtectedPrincipalRole, action: ProtectedAction
) -> None:
    subject = _principal("role-subject", role)
    issuer = (
        _approval_principal("hazelnutflavoured", ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER)
        if role is ProtectedPrincipalRole.DATASET_CUSTODIAN
        else _approval_principal()
    )
    grant = _grant(subject=subject, issuer=issuer, actions=(action,))

    with pytest.raises(ProtectedSecurityError, match="ACTION_NOT_GRANTED"):
        InMemoryApprovalEvidenceVerifier(
            {grant.approval_source_event_id: _approval_source(grant, issuer=issuer)}
        ).verify(grant)


def test_authorization_and_operation_audits_have_distinct_exact_fields() -> None:
    grant = _grant()
    _, journal, _, _, _ = _authorized_components(grant, _approval_source(grant))
    entry = journal.entries[0]

    assert isinstance(entry, AuthorizationAuditEntry)
    assert entry.action is AuthorizationAuditAction.GRANT
    assert entry.effective_revision == grant.revision
    assert entry.dataset_id == grant.dataset_id
    assert entry.dataset_version == grant.dataset_version
    assert entry.manifest_sha256 == grant.manifest_sha256
    assert entry.protected_artifact_sha256 == grant.protected_artifact_sha256
    assert entry.hmac_key_version == grant.hmac_key_version
    assert entry.actions == grant.actions
    assert entry.approval_source_raw_sha256 == grant.approval_source_raw_sha256
    assert entry.valid_from == grant.valid_from
    assert entry.expires_at == grant.expires_at
    assert not hasattr(entry, "outcome")

    with pytest.raises(ValidationError):
        type(entry).model_validate({**entry.model_dump(), "outcome": OperationAuditOutcome.SUCCEEDED})


@pytest.mark.parametrize(
    "update",
    [
        {"effective_revision": 2},
        {"dataset_id": "different-dataset"},
        {"dataset_version": "2.0.0"},
        {"manifest_sha256": "d" * 64},
        {"protected_artifact_sha256": "d" * 64},
        {"hmac_key_version": "different-key-version"},
        {"actions": (ProtectedAction.READ,)},
        {"approval_source_raw_sha256": "d" * 64},
        {"valid_from": NOW - timedelta(minutes=2)},
        {"expires_at": NOW + timedelta(minutes=20)},
    ],
)
def test_authorization_audit_detects_grant_binding_tamper(update: dict[str, object]) -> None:
    grant = _grant()
    _, journal, _, _, _ = _authorized_components(grant, _approval_source(grant))
    entry = journal.entries[0]
    assert isinstance(entry, AuthorizationAuditEntry)
    journal.entries[0] = entry.model_copy(update=update)

    with pytest.raises(ProtectedSecurityError, match="AUDIT_HASH_MISMATCH"):
        journal.verify_chain()


@pytest.mark.asyncio
async def test_authorized_write_records_intent_and_success() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))

    result = await execute_protected_operation(
        _request(), ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
    )

    assert result.reason_code == "PROTECTED_OPERATION_SUCCEEDED"
    assert operation.call_count == 1
    assert [entry.outcome for entry in journal.operation_entries] == [
        OperationAuditOutcome.INTENT,
        OperationAuditOutcome.SUCCEEDED,
    ]
    assert journal.operation_entries[-1].result_ref == result.result_ref
    assert journal.operation_entries[-1].dataset_id == grant.dataset_id
    assert journal.operation_entries[-1].dataset_version == grant.dataset_version
    assert journal.operation_entries[-1].manifest_sha256 == grant.manifest_sha256
    assert journal.operation_entries[-1].hmac_key_version == grant.hmac_key_version


@pytest.mark.asyncio
async def test_higher_revision_grants_with_other_subject_or_dataset_binding_do_not_shadow_valid_grant() -> None:
    requested_dataset = _dataset(dataset_version="1.0.0")
    other_version = _dataset(dataset_version="2.0.0", artifact_sha256="d" * 64)
    requested_subject = _principal("requested-author")
    valid_grant = _grant(subject=requested_subject, dataset=requested_dataset).model_copy(
        update={"approval_source_event_id": "review-valid"}
    )
    other_subject_grant = _grant(subject=_principal("other-author"), dataset=requested_dataset).model_copy(
        update={"revision": 8, "approval_source_event_id": "review-other-subject"}
    )
    other_binding_grant = _grant(subject=requested_subject, dataset=other_version).model_copy(
        update={"revision": 9, "approval_source_event_id": "review-other-binding"}
    )
    sources = {
        source.source_event_id: source
        for source in (
            _approval_source(valid_grant, source_event_id="review-valid"),
            _approval_source(other_subject_grant, source_event_id="review-other-subject"),
            _approval_source(other_binding_grant, source_event_id="review-other-binding"),
        )
    }
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    ledger = InMemoryAuthorizationLedger(journal, clock, InMemoryApprovalEvidenceVerifier(sources))
    ledger.register_dataset(requested_dataset)
    ledger.register_dataset(other_version)
    await ledger.grant(valid_grant)
    await ledger.grant(other_subject_grant)
    await ledger.grant(other_binding_grant)
    operation = SyntheticProtectedOperation()

    result = await execute_protected_operation(
        _request(principal=requested_subject, dataset=requested_dataset),
        ledger=ledger,
        guard=InMemoryAuthorizationGuard(ledger, clock),
        journal=journal,
        operation=operation,
        clock=clock,
    )

    assert result.reason_code == "PROTECTED_OPERATION_SUCCEEDED"
    assert operation.call_count == 1
    assert journal.operation_entries[-1].grant_id == valid_grant.grant_id


@pytest.mark.asyncio
async def test_higher_revision_grants_outside_the_validity_window_do_not_shadow_valid_grant() -> None:
    dataset = _dataset()
    subject = _principal("requested-author")
    valid_grant = _grant(subject=subject, dataset=dataset).model_copy(
        update={"approval_source_event_id": "review-valid-window"}
    )
    expired_grant = _grant(subject=subject, dataset=dataset).model_copy(
        update={
            "revision": 8,
            "approval_source_event_id": "review-expired-window",
            "valid_from": NOW - timedelta(minutes=20),
            "expires_at": NOW,
        }
    )
    future_grant = _grant(subject=subject, dataset=dataset).model_copy(
        update={
            "revision": 9,
            "approval_source_event_id": "review-future-window",
            "valid_from": NOW + timedelta(minutes=1),
            "expires_at": NOW + timedelta(minutes=20),
        }
    )
    sources = {
        source.source_event_id: source
        for source in (
            _approval_source(valid_grant, source_event_id="review-valid-window"),
            _approval_source(expired_grant, source_event_id="review-expired-window"),
            _approval_source(future_grant, source_event_id="review-future-window"),
        )
    }
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    ledger = InMemoryAuthorizationLedger(journal, clock, InMemoryApprovalEvidenceVerifier(sources))
    ledger.register_dataset(dataset)
    await ledger.grant(valid_grant)
    await ledger.grant(expired_grant)
    await ledger.grant(future_grant)
    operation = SyntheticProtectedOperation()

    result = await execute_protected_operation(
        _request(principal=subject, dataset=dataset),
        ledger=ledger,
        guard=InMemoryAuthorizationGuard(ledger, clock),
        journal=journal,
        operation=operation,
        clock=clock,
    )

    assert result.reason_code == "PROTECTED_OPERATION_SUCCEEDED"
    assert operation.call_count == 1
    assert journal.operation_entries[-1].grant_id == valid_grant.grant_id
    assert ledger.current(expired_grant.grant_id) is None
    assert ledger.current_revision(expired_grant.grant_id) == expired_grant.revision + 1
    assert any(
        isinstance(entry, AuthorizationAuditEntry)
        and entry.grant_id == expired_grant.grant_id
        and entry.action is AuthorizationAuditAction.EXPIRE
        and entry.effective_revision == expired_grant.revision + 1
        for entry in journal.entries
    )


@pytest.mark.asyncio
async def test_completed_operation_returns_the_audited_result_without_reexecution() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    request = _request(operation_key="idempotent-operation")

    first = await execute_protected_operation(
        request, ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
    )
    second = await execute_protected_operation(
        request.model_copy(update={"request_id": str(uuid4())}),
        ledger=ledger,
        guard=guard,
        journal=journal,
        operation=operation,
        clock=clock,
    )

    assert second == first
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_completed_operation_replays_with_its_still_current_grant_after_a_newer_grant_is_added() -> None:
    dataset = _dataset()
    subject = _principal("requested-author")
    original_grant = _grant(subject=subject, dataset=dataset).model_copy(
        update={"approval_source_event_id": "review-original"}
    )
    newer_grant = _grant(subject=subject, dataset=dataset).model_copy(
        update={"revision": 2, "approval_source_event_id": "review-newer"}
    )
    sources = {
        source.source_event_id: source
        for source in (
            _approval_source(original_grant, source_event_id="review-original"),
            _approval_source(newer_grant, source_event_id="review-newer"),
        )
    }
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    ledger = InMemoryAuthorizationLedger(journal, clock, InMemoryApprovalEvidenceVerifier(sources))
    ledger.register_dataset(dataset)
    await ledger.grant(original_grant)
    operation = SyntheticProtectedOperation()
    request = _request(principal=subject, dataset=dataset, operation_key="grant-stable-replay")

    first = await execute_protected_operation(
        request,
        ledger=ledger,
        guard=InMemoryAuthorizationGuard(ledger, clock),
        journal=journal,
        operation=operation,
        clock=clock,
    )
    await ledger.grant(newer_grant)
    second = await execute_protected_operation(
        request.model_copy(update={"request_id": str(uuid4())}),
        ledger=ledger,
        guard=InMemoryAuthorizationGuard(ledger, clock),
        journal=journal,
        operation=operation,
        clock=clock,
    )

    assert second == first
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_revoked_completed_operation_replay_records_a_separate_denial_attempt() -> None:
    grant = _grant()
    grant_source = _approval_source(grant)
    revoke_source = _approval_source(
        grant,
        action=AuthorizationAuditAction.REVOKE,
        source_event_id="review-revoke-completed-operation",
    )
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    ledger = InMemoryAuthorizationLedger(
        journal,
        clock,
        InMemoryApprovalEvidenceVerifier(
            {
                grant_source.source_event_id: grant_source,
                revoke_source.source_event_id: revoke_source,
            }
        ),
    )
    ledger.register_dataset(_dataset())
    await ledger.grant(grant)
    guard = InMemoryAuthorizationGuard(ledger, clock)
    operation = SyntheticProtectedOperation()
    request = _request(operation_key="revoked-completed-operation")
    await execute_protected_operation(
        request,
        ledger=ledger,
        guard=guard,
        journal=journal,
        operation=operation,
        clock=clock,
    )
    await ledger.revoke(grant.grant_id, revoke_source.source_event_id, SHA_B)

    replay_request_id = str(uuid4())
    with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_REVOKED"):
        await execute_protected_operation(
            request.model_copy(update={"request_id": replay_request_id}),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )

    assert [entry.outcome for entry in journal.operation_entries] == [
        OperationAuditOutcome.INTENT,
        OperationAuditOutcome.SUCCEEDED,
        OperationAuditOutcome.DENIED,
    ]
    denial = journal.operation_entries[-1]
    assert denial.request_id == replay_request_id
    assert denial.closes_intent is False
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_denied_operation_key_from_another_principal_does_not_block_authorized_request() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    operation_key = "principal-scoped-operation"

    with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
        await execute_protected_operation(
            _request(principal=_principal("other-author"), operation_key=operation_key),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )

    result = await execute_protected_operation(
        _request(operation_key=operation_key),
        ledger=ledger,
        guard=guard,
        journal=journal,
        operation=operation,
        clock=clock,
    )

    assert result.reason_code == "PROTECTED_OPERATION_SUCCEEDED"
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_repeated_denials_for_the_same_operation_scope_are_each_audited() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    denied_request = _request(principal=_principal("other-author"), operation_key="repeated-denial")
    second_request_id = str(uuid4())

    for request_id in (denied_request.request_id, second_request_id):
        with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
            await execute_protected_operation(
                denied_request.model_copy(update={"request_id": request_id}),
                ledger=ledger,
                guard=guard,
                journal=journal,
                operation=operation,
                clock=clock,
            )

    denied = [entry for entry in journal.operation_entries if entry.outcome is OperationAuditOutcome.DENIED]
    assert len(denied) == 2
    assert {entry.request_id for entry in denied} == {denied_request.request_id, second_request_id}


@pytest.mark.asyncio
async def test_concurrent_duplicate_operation_replays_after_the_guard_is_acquired() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    operation.pause_before_return = asyncio.Event()
    operation.resume = asyncio.Event()
    first_request = _request(operation_key="concurrent-idempotent-operation")
    second_request = first_request.model_copy(update={"request_id": str(uuid4())})

    first_execution = asyncio.create_task(
        execute_protected_operation(
            first_request,
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    )
    await operation.pause_before_return.wait()
    second_execution = asyncio.create_task(
        execute_protected_operation(
            second_request,
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    )
    await asyncio.sleep(0)
    assert not second_execution.done()

    operation.resume.set()
    first, second = await asyncio.gather(first_execution, second_execution)

    assert second == first
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_replays_the_audited_grant_after_a_newer_grant_commits() -> None:
    dataset = _dataset()
    original_grant = _grant(dataset=dataset).model_copy(
        update={"approval_source_event_id": "review-concurrent-original"}
    )
    newer_grant = _grant(dataset=dataset).model_copy(
        update={"revision": 2, "approval_source_event_id": "review-concurrent-newer"}
    )
    sources = {
        source.source_event_id: source
        for source in (
            _approval_source(original_grant, source_event_id="review-concurrent-original"),
            _approval_source(newer_grant, source_event_id="review-concurrent-newer"),
        )
    }
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    ledger = InMemoryAuthorizationLedger(journal, clock, InMemoryApprovalEvidenceVerifier(sources))
    ledger.register_dataset(dataset)
    await ledger.grant(original_grant)
    guard = InMemoryAuthorizationGuard(ledger, clock)
    operation = SyntheticProtectedOperation()
    operation.pause_before_return = asyncio.Event()
    operation.resume = asyncio.Event()
    request = _request(dataset=dataset, operation_key="concurrent-grant-stable-replay")
    first_execution = asyncio.create_task(
        execute_protected_operation(
            request,
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    )
    await operation.pause_before_return.wait()
    newer_grant_commit = asyncio.create_task(ledger.grant(newer_grant))
    await asyncio.sleep(0)
    assert not newer_grant_commit.done()
    duplicate = asyncio.create_task(
        execute_protected_operation(
            request.model_copy(update={"request_id": str(uuid4())}),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    )

    operation.resume.set()
    first = await first_execution
    await newer_grant_commit
    second = await duplicate

    assert second == first
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_completed_operation_result_is_not_revealed_to_a_different_binding() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    request = _request(operation_key="bound-idempotent-operation")
    first = await execute_protected_operation(
        request, ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
    )

    with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
        await execute_protected_operation(
            request.model_copy(update={"principal": _principal("different-author")}),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    different_target = await execute_protected_operation(
        request.model_copy(
            update={
                "request_id": str(uuid4()),
                "target_ref": _ref(OpaqueRefNamespace.HOLDOUT_SET),
            }
        ),
        ledger=ledger,
        guard=guard,
        journal=journal,
        operation=operation,
        clock=clock,
    )

    assert different_target != first
    assert operation.call_count == 2


@pytest.mark.asyncio
async def test_intent_audit_failure_never_calls_operation() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    journal.fail_next_append = True

    with pytest.raises(ProtectedSecurityError, match="AUDIT_UNAVAILABLE"):
        await execute_protected_operation(
            _request(), ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
        )

    assert operation.call_count == 0


@pytest.mark.asyncio
async def test_denial_audit_failure_is_fail_closed_and_does_not_call_operation() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    journal.fail_next_append = True

    with pytest.raises(ProtectedSecurityError, match="AUDIT_UNAVAILABLE"):
        await execute_protected_operation(
            _request(principal=_principal("different-author")),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )

    assert operation.call_count == 0


@pytest.mark.asyncio
async def test_security_errors_never_expose_caller_supplied_details() -> None:
    error = ProtectedSecurityError("/protected/location?query=secret")

    assert str(error) == "INTERNAL_ERROR"
    assert "protected" not in str(error).lower()

    journal = InMemoryProtectedAuditJournal(FixedTrustedClock(NOW))
    with pytest.raises(ProtectedSecurityError, match="INTERNAL_ERROR"):
        await journal.append_operation(_request(), None, OperationAuditOutcome.DENIED, "/protected/query=secret")
    assert journal.entries == []


@pytest.mark.asyncio
async def test_terminal_audit_failure_never_returns_success() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    journal.fail_on_outcomes.add(OperationAuditOutcome.SUCCEEDED)

    with pytest.raises(ProtectedSecurityError, match="OPERATION_OUTCOME_UNKNOWN"):
        await execute_protected_operation(
            _request(), ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
        )

    assert operation.call_count == 1
    assert journal.operation_entries[-1].outcome is OperationAuditOutcome.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_after_side_effect", "failed_audit_outcomes"),
    [
        (True, {OperationAuditOutcome.UNKNOWN}),
        (
            False,
            {OperationAuditOutcome.SUCCEEDED, OperationAuditOutcome.UNKNOWN},
        ),
    ],
    ids=("operation-failure-unknown-audit-failure", "terminal-and-unknown-audit-failure"),
)
async def test_post_execution_audit_failure_keeps_intent_unresolved(
    fail_after_side_effect: bool,
    failed_audit_outcomes: set[OperationAuditOutcome],
) -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    operation.fail_after_side_effect = fail_after_side_effect
    journal.fail_on_outcomes.update(failed_audit_outcomes)
    request = _request(operation_key="post-execution-audit-failure")

    with pytest.raises(ProtectedSecurityError, match="AUDIT_UNAVAILABLE"):
        await execute_protected_operation(
            request, ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
        )

    assert [entry.outcome for entry in journal.operation_entries] == [OperationAuditOutcome.INTENT]

    with pytest.raises(ProtectedSecurityError, match="RECONCILIATION_REQUIRED"):
        await execute_protected_operation(
            request.model_copy(update={"request_id": str(uuid4())}),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    assert operation.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_request", "reason"),
    [
        (_request(principal=_principal("other-author")), "AUTHORIZATION_NOT_FOUND"),
        (_request(dataset=_dataset(state=ProtectedDatasetState.FROZEN)), "DATASET_STATE_MISMATCH"),
        (
            _request(
                principal=_principal("runner", ProtectedPrincipalRole.PROTECTED_RUNNER),
                dataset=_dataset(state=ProtectedDatasetState.AUTHORING),
                action=ProtectedAction.RUN,
            ),
            "ROLE_ACTION_STATE_DENIED",
        ),
    ],
)
async def test_unauthorized_identity_or_state_never_calls_operation(
    operation_request: ProtectedOperationRequest, reason: str
) -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))

    with pytest.raises(ProtectedSecurityError, match=reason):
        await execute_protected_operation(
            operation_request,
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )

    assert operation.call_count == 0
    assert journal.operation_entries[-1].outcome is OperationAuditOutcome.DENIED


@pytest.mark.asyncio
async def test_freeze_and_run_require_all_state_evidence() -> None:
    custodian = _principal("phina-io", ProtectedPrincipalRole.DATASET_CUSTODIAN)
    safety_issuer = _approval_principal("hazelnutflavoured", ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER)
    incomplete = _dataset(state=ProtectedDatasetState.REVIEW_READY, authored_count=40, review_complete=True)
    freeze_grant = _grant(
        subject=custodian,
        issuer=safety_issuer,
        dataset=incomplete,
        actions=(ProtectedAction.FREEZE,),
    )
    clock, journal, ledger, guard, operation = _authorized_components(
        freeze_grant, _approval_source(freeze_grant, issuer=safety_issuer), dataset=incomplete
    )

    with pytest.raises(ProtectedSecurityError, match="FREEZE_EVIDENCE_INCOMPLETE"):
        await execute_protected_operation(
            _request(principal=custodian, dataset=incomplete, action=ProtectedAction.FREEZE),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )

    runner = _principal("runner", ProtectedPrincipalRole.PROTECTED_RUNNER)
    frozen = _dataset(state=ProtectedDatasetState.FROZEN)
    run_grant = _grant(subject=runner, dataset=frozen, actions=(ProtectedAction.RUN,))
    clock, journal, ledger, guard, operation = _authorized_components(
        run_grant, _approval_source(run_grant), dataset=frozen
    )
    with pytest.raises(ProtectedSecurityError, match="RUN_EVIDENCE_INCOMPLETE"):
        await execute_protected_operation(
            _request(principal=runner, dataset=frozen, action=ProtectedAction.RUN),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    assert operation.call_count == 0


@pytest.mark.asyncio
async def test_runner_read_requires_the_same_freeze_and_execution_evidence_as_run() -> None:
    runner = _principal("runner", ProtectedPrincipalRole.PROTECTED_RUNNER)
    frozen = _dataset(state=ProtectedDatasetState.FROZEN)
    grant = _grant(subject=runner, dataset=frozen, actions=(ProtectedAction.READ,))
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant), dataset=frozen)

    with pytest.raises(ProtectedSecurityError, match="RUN_EVIDENCE_INCOMPLETE"):
        await execute_protected_operation(
            _request(principal=runner, dataset=frozen, action=ProtectedAction.READ),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    assert operation.call_count == 0


@pytest.mark.asyncio
async def test_request_cannot_self_assert_a_new_dataset_state_or_receipts() -> None:
    runner = _principal("runner", ProtectedPrincipalRole.PROTECTED_RUNNER)
    authoritative = _dataset(state=ProtectedDatasetState.AUTHORING)
    claimed = _dataset(
        state=ProtectedDatasetState.FROZEN,
        freeze_receipt_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
        execution_authorization_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
        retriever_binding_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
    )
    grant = _grant(subject=runner, dataset=authoritative, actions=(ProtectedAction.RUN,))
    clock, journal, ledger, guard, operation = _authorized_components(
        grant, _approval_source(grant), dataset=authoritative
    )

    with pytest.raises(ProtectedSecurityError, match="DATASET_STATE_MISMATCH"):
        await execute_protected_operation(
            _request(principal=runner, dataset=claimed, action=ProtectedAction.RUN),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    assert operation.call_count == 0


@pytest.mark.asyncio
async def test_revocation_inside_guard_prevents_operation() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    guard.revoke_before_consume = True

    with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_REVOKED"):
        await execute_protected_operation(
            _request(), ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
        )

    assert operation.call_count == 0
    assert [entry.outcome for entry in journal.operation_entries] == [
        OperationAuditOutcome.INTENT,
        OperationAuditOutcome.DENIED,
    ]


@pytest.mark.asyncio
async def test_intent_or_unknown_requires_reconciliation_even_with_a_new_request_id() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    operation.fail_after_side_effect = True
    request = _request(operation_key="same-operation")

    with pytest.raises(ProtectedSecurityError, match="OPERATION_OUTCOME_UNKNOWN"):
        await execute_protected_operation(
            request, ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
        )

    with pytest.raises(ProtectedSecurityError, match="RECONCILIATION_REQUIRED"):
        await execute_protected_operation(
            request.model_copy(update={"request_id": str(uuid4())}),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_unresolved_intent_retry_preserves_reconciliation_error_and_audits_denial() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    original = _request(operation_key="unresolved-intent")
    await journal.append_operation(original, grant, OperationAuditOutcome.INTENT, "AUTHORIZED")
    retry_request_id = str(uuid4())

    with pytest.raises(ProtectedSecurityError, match="RECONCILIATION_REQUIRED"):
        await execute_protected_operation(
            original.model_copy(update={"request_id": retry_request_id}),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )

    assert [entry.outcome for entry in journal.operation_entries] == [
        OperationAuditOutcome.INTENT,
        OperationAuditOutcome.DENIED,
    ]
    denial = journal.operation_entries[-1]
    assert denial.request_id == retry_request_id
    assert denial.closes_intent is False
    assert operation.call_count == 0


@pytest.mark.asyncio
async def test_audit_chain_detects_tamper_and_rejects_duplicate_terminal() -> None:
    grant = _grant()
    clock, journal, _, _, _ = _authorized_components(grant, _approval_source(grant))
    request = _request()
    await journal.append_operation(request, grant, OperationAuditOutcome.INTENT, "AUTHORIZED")
    await journal.append_operation(request, grant, OperationAuditOutcome.SUCCEEDED, "COMPLETED", result=_result())

    journal.verify_chain()
    journal.entries[1] = journal.entries[1].model_copy(update={"reason_code": ProtectedAuditReason.REVOKED})
    with pytest.raises(ProtectedSecurityError, match="AUDIT_HASH_MISMATCH"):
        journal.verify_chain()

    clean = InMemoryProtectedAuditJournal(clock)
    await clean.append_operation(request, grant, OperationAuditOutcome.INTENT, "AUTHORIZED")
    await clean.append_operation(request, grant, OperationAuditOutcome.SUCCEEDED, "COMPLETED", result=_result())
    with pytest.raises(ProtectedSecurityError, match="AUDIT_TRANSITION_INVALID"):
        await clean.append_operation(request, grant, OperationAuditOutcome.SUCCEEDED, "COMPLETED", result=_result())


@pytest.mark.asyncio
async def test_audit_chain_detects_tail_truncation_and_terminal_binding_changes() -> None:
    grant = _grant()
    clock, journal, _, _, _ = _authorized_components(grant, _approval_source(grant))
    request = _request()
    await journal.append_operation(request, grant, OperationAuditOutcome.INTENT, "AUTHORIZED")
    other_request = request.model_copy(update={"request_id": str(uuid4())})

    with pytest.raises(ProtectedSecurityError, match="AUDIT_BINDING_MISMATCH"):
        await journal.append_operation(other_request, grant, OperationAuditOutcome.SUCCEEDED, "COMPLETED")

    journal.entries.pop()
    with pytest.raises(ProtectedSecurityError, match="AUDIT_TAIL_TRUNCATED"):
        journal.verify_chain()


@pytest.mark.asyncio
async def test_audit_tail_truncation_blocks_the_next_operation_before_side_effect() -> None:
    grant = _grant()
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    await execute_protected_operation(
        _request(operation_key="first-operation"),
        ledger=ledger,
        guard=guard,
        journal=journal,
        operation=operation,
        clock=clock,
    )
    journal.entries.pop()

    with pytest.raises(ProtectedSecurityError, match="AUDIT_TAIL_TRUNCATED"):
        await execute_protected_operation(
            _request(operation_key="second-operation"),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )

    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_revoke_cannot_commit_while_an_operation_holds_the_guard() -> None:
    grant = _grant()
    grant_source = _approval_source(grant)
    revoke_source = _approval_source(
        grant,
        action=AuthorizationAuditAction.REVOKE,
        source_event_id="github-pr-review-revoke-789",
    )
    verifier = InMemoryApprovalEvidenceVerifier(
        {grant_source.source_event_id: grant_source, revoke_source.source_event_id: revoke_source}
    )
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    ledger = InMemoryAuthorizationLedger(journal, clock, verifier)
    ledger.register_dataset(_dataset())
    await ledger.grant(grant)
    guard = InMemoryAuthorizationGuard(ledger, clock)
    operation = SyntheticProtectedOperation()
    operation.pause_before_return = asyncio.Event()
    operation.resume = asyncio.Event()

    execution = asyncio.create_task(
        execute_protected_operation(
            _request(), ledger=ledger, guard=guard, journal=journal, operation=operation, clock=clock
        )
    )
    await operation.pause_before_return.wait()
    revocation = asyncio.create_task(ledger.revoke(grant.grant_id, revoke_source.source_event_id, SHA_B))
    await asyncio.sleep(0)
    assert not revocation.done()

    operation.resume.set()
    await execution
    await revocation
    assert ledger.current(grant.grant_id) is None


@pytest.mark.asyncio
async def test_expiry_transition_waits_until_the_guarded_operation_commits() -> None:
    grant = _grant().model_copy(update={"expires_at": NOW + timedelta(seconds=1)})
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant))
    operation.pause_before_return = asyncio.Event()
    operation.resume = asyncio.Event()
    execution = asyncio.create_task(
        execute_protected_operation(
            _request(operation_key="operation-before-expiry"),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    )
    await operation.pause_before_return.wait()
    clock.advance(timedelta(seconds=2))
    expiry_observation = asyncio.create_task(
        execute_protected_operation(
            _request(operation_key="operation-after-expiry"),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    )
    await asyncio.sleep(0)
    assert not expiry_observation.done()
    assert not any(
        isinstance(entry, AuthorizationAuditEntry) and entry.action is AuthorizationAuditAction.EXPIRE
        for entry in journal.entries
    )

    operation.resume.set()
    await execution
    with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_NOT_FOUND"):
        await expiry_observation
    outcomes = [
        entry.action if isinstance(entry, AuthorizationAuditEntry) else entry.outcome for entry in journal.entries
    ]
    assert outcomes.index(OperationAuditOutcome.SUCCEEDED) < outcomes.index(AuthorizationAuditAction.EXPIRE)


def test_authoritative_dataset_registration_cannot_replace_existing_state() -> None:
    grant = _grant()
    _, _, ledger, _, _ = _authorized_components(grant, _approval_source(grant))

    with pytest.raises(ProtectedSecurityError, match="DATASET_STATE_MISMATCH"):
        ledger.register_dataset(_dataset(state=ProtectedDatasetState.FROZEN, state_revision=4))


@pytest.mark.asyncio
async def test_dataset_transition_waits_until_the_guarded_operation_commits() -> None:
    initial = _dataset()
    grant = _grant(dataset=initial)
    clock, journal, ledger, guard, operation = _authorized_components(grant, _approval_source(grant), dataset=initial)
    operation.pause_before_return = asyncio.Event()
    operation.resume = asyncio.Event()
    execution = asyncio.create_task(
        execute_protected_operation(
            _request(dataset=initial),
            ledger=ledger,
            guard=guard,
            journal=journal,
            operation=operation,
            clock=clock,
        )
    )
    await operation.pause_before_return.wait()
    updated = initial.model_copy(
        update={"state": ProtectedDatasetState.REVIEW_READY, "state_revision": initial.state_revision + 1}
    )
    transition = asyncio.create_task(ledger.transition_dataset(initial, updated))
    await asyncio.sleep(0)
    assert not transition.done()

    operation.resume.set()
    await execution
    await transition
    assert journal.operation_entries[-1].outcome is OperationAuditOutcome.SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updated_state",
    [ProtectedDatasetState.AUTHORING, ProtectedDatasetState.REVIEW_READY],
)
async def test_frozen_dataset_cannot_transition_back_to_an_authoring_state(
    updated_state: ProtectedDatasetState,
) -> None:
    frozen = _dataset(
        state=ProtectedDatasetState.FROZEN,
        freeze_receipt_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
        execution_authorization_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
        retriever_binding_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
    )
    grant = _grant(dataset=frozen)
    _, _, ledger, _, _ = _authorized_components(grant, _approval_source(grant), dataset=frozen)
    reopened = frozen.model_copy(update={"state": updated_state, "state_revision": frozen.state_revision + 1})

    with pytest.raises(ProtectedSecurityError, match="DATASET_STATE_MISMATCH"):
        await ledger.transition_dataset(frozen, reopened)
    assert await ledger.require_dataset(_request(dataset=frozen)) == frozen


@pytest.mark.asyncio
async def test_frozen_dataset_cannot_remove_freeze_evidence() -> None:
    frozen = _dataset(
        state=ProtectedDatasetState.FROZEN,
        freeze_receipt_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
        execution_authorization_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
        retriever_binding_ref=_ref(OpaqueRefNamespace.AUDIT_EVENT),
    )
    grant = _grant(dataset=frozen)
    _, _, ledger, _, _ = _authorized_components(grant, _approval_source(grant), dataset=frozen)
    changed = frozen.model_copy(update={"freeze_receipt_ref": None, "state_revision": frozen.state_revision + 1})

    with pytest.raises(ProtectedSecurityError, match="DATASET_STATE_MISMATCH"):
        await ledger.transition_dataset(frozen, changed)
    assert await ledger.require_dataset(_request(dataset=frozen)) == frozen


@pytest.mark.asyncio
async def test_expired_grant_uses_the_trusted_clock() -> None:
    grant = _grant().model_copy(update={"expires_at": NOW})
    clock = FixedTrustedClock(NOW)
    journal = InMemoryProtectedAuditJournal(clock)
    source = _approval_source(grant)
    ledger = InMemoryAuthorizationLedger(
        journal, clock, InMemoryApprovalEvidenceVerifier({source.source_event_id: source})
    )
    ledger.register_dataset(_dataset())
    await ledger.grant(grant)

    with pytest.raises(ProtectedSecurityError, match="AUTHORIZATION_EXPIRED"):
        await ledger.require_current(grant.grant_id)
    assert ledger.current_revision(grant.grant_id) == grant.revision + 1
    entry = journal.entries[-1]
    assert isinstance(entry, AuthorizationAuditEntry)
    assert entry.action is AuthorizationAuditAction.EXPIRE
    assert entry.effective_revision == grant.revision + 1
