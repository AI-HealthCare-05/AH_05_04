from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ai_worker.tasks.evaluation.protected_retrieval import (
    ApprovalSourceEvidence,
    AuthorizationAuditAction,
    AuthorizationAuditEntry,
    OpaqueLogicalRef,
    OpaqueRefNamespace,
    OperationAuditEntry,
    OperationAuditOutcome,
    ProtectedAction,
    ProtectedApprovalRole,
    ProtectedAuditEntry,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationCapability,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedOperationRequest,
    ProtectedOperationResult,
    ProtectedPrincipalRole,
    ProtectedSecurityError,
    VerifiedAuthorizationApproval,
    audit_entry_sha256,
    authorization_grant_approval_sha256,
    authorization_grant_sha256,
    new_event_id,
)


class FixedTrustedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("trusted clock must be UTC-aware")
        self._value = value.astimezone(UTC)

    def now_utc(self) -> datetime:
        return self._value

    def advance(self, delta: timedelta) -> None:
        self._value += delta


class InMemoryApprovalEvidenceVerifier:
    def __init__(self, trusted_sources: dict[str, ApprovalSourceEvidence]) -> None:
        self._trusted_sources = dict(trusted_sources)

    def verify(
        self,
        grant: ProtectedAuthorizationGrant,
        *,
        presented: ApprovalSourceEvidence | None = None,
    ) -> VerifiedAuthorizationApproval:
        return self._verify(
            grant,
            source_event_id=grant.approval_source_event_id,
            expected_raw_sha256=grant.approval_source_raw_sha256,
            action=AuthorizationAuditAction.GRANT,
            presented=presented,
        )

    def verify_revoke(
        self,
        grant: ProtectedAuthorizationGrant,
        source_event_id: str,
        expected_raw_sha256: str,
        *,
        presented: ApprovalSourceEvidence | None = None,
    ) -> VerifiedAuthorizationApproval:
        return self._verify(
            grant,
            source_event_id=source_event_id,
            expected_raw_sha256=expected_raw_sha256,
            action=AuthorizationAuditAction.REVOKE,
            presented=presented,
        )

    def _verify(
        self,
        grant: ProtectedAuthorizationGrant,
        *,
        source_event_id: str,
        expected_raw_sha256: str,
        action: AuthorizationAuditAction,
        presented: ApprovalSourceEvidence | None,
    ) -> VerifiedAuthorizationApproval:
        trusted = self._trusted_sources.get(source_event_id)
        if trusted is None or (presented is not None and presented != trusted):
            raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
        if trusted.authorization_action is not action:
            raise ProtectedSecurityError("APPROVAL_ACTION_MISMATCH")
        binding = grant.control_implementation
        if (
            trusted.state != "APPROVED"
            or trusted.issuer != grant.issuer
            or trusted.target_commit_oid != binding.commit_oid
            or trusted.target_artifact_sha256 != binding.artifact_sha256
            or trusted.canonical_raw_sha256 != expected_raw_sha256
            or trusted.implementation_participants != binding.participants
        ):
            raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
        if trusted.approved_grant_payload_sha256 != authorization_grant_approval_sha256(grant):
            raise ProtectedSecurityError("APPROVAL_GRANT_BINDING_MISMATCH")
        if trusted.issuer.actor == grant.subject.actor or trusted.issuer.actor in binding.participants:
            raise ProtectedSecurityError("SELF_APPROVAL_DENIED")
        expected_issuer_role = (
            ProtectedApprovalRole.PRODUCT_SAFETY_REVIEWER
            if grant.subject.role is ProtectedPrincipalRole.DATASET_CUSTODIAN
            else ProtectedApprovalRole.DATASET_CUSTODIAN
        )
        if trusted.issuer.role is not expected_issuer_role:
            raise ProtectedSecurityError("ISSUER_ROLE_DENIED")
        allowed_actions = {
            ProtectedPrincipalRole.HOLDOUT_AUTHOR: {ProtectedAction.READ, ProtectedAction.WRITE},
            ProtectedPrincipalRole.DATASET_CUSTODIAN: {ProtectedAction.READ, ProtectedAction.FREEZE},
            ProtectedPrincipalRole.PROTECTED_RUNNER: {ProtectedAction.READ, ProtectedAction.RUN},
        }
        if not set(grant.actions) <= allowed_actions[grant.subject.role]:
            raise ProtectedSecurityError("ACTION_NOT_GRANTED")
        return VerifiedAuthorizationApproval._from_verified(trusted, grant, action)


class InMemoryProtectedAuditJournal:
    def __init__(self, clock: FixedTrustedClock) -> None:
        self._clock = clock
        self.entries: list[ProtectedAuditEntry] = []
        self.fail_next_append = False
        self.fail_on_outcomes: set[OperationAuditOutcome] = set()
        self._durable_sequence = 0
        self._durable_head: str | None = None

    @property
    def operation_entries(self) -> list[OperationAuditEntry]:
        return [entry for entry in self.entries if isinstance(entry, OperationAuditEntry)]

    def _append(self, entry: ProtectedAuditEntry) -> ProtectedAuditEntry:
        self.verify_chain()
        if isinstance(entry, OperationAuditEntry) and entry.outcome in self.fail_on_outcomes:
            raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
        if self.fail_next_append:
            self.fail_next_append = False
            raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
        expected_sequence = self._durable_sequence + 1
        expected_previous = self._durable_head
        if entry.sequence != expected_sequence or entry.previous_entry_sha256 != expected_previous:
            raise ProtectedSecurityError("AUDIT_CAS_CONFLICT")
        if audit_entry_sha256(entry) != entry.entry_sha256:
            raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
        if any(existing.event_id == entry.event_id for existing in self.entries):
            raise ProtectedSecurityError("AUDIT_CAS_CONFLICT")
        if self.entries and entry.recorded_at < self.entries[-1].recorded_at:
            raise ProtectedSecurityError("AUDIT_CAS_CONFLICT")
        self.entries.append(entry)
        self._durable_sequence = entry.sequence
        self._durable_head = entry.entry_sha256
        return entry

    def append_authorization(
        self,
        grant: ProtectedAuthorizationGrant,
        action: AuthorizationAuditAction,
        reason_code: ProtectedAuditReason | str,
        *,
        approval: VerifiedAuthorizationApproval | None = None,
    ) -> AuthorizationAuditEntry:
        prior = [
            entry
            for entry in self.entries
            if isinstance(entry, AuthorizationAuditEntry) and entry.grant_id == grant.grant_id
        ]
        if action is AuthorizationAuditAction.GRANT and prior:
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
        if action in {AuthorizationAuditAction.REVOKE, AuthorizationAuditAction.EXPIRE} and (
            not prior or prior[-1].action is not AuthorizationAuditAction.GRANT
        ):
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
        try:
            safe_reason = ProtectedAuditReason(reason_code)
        except ValueError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None
        entry = AuthorizationAuditEntry(
            event_kind=ProtectedAuditEventKind.AUTHORIZATION,
            sequence=self._durable_sequence + 1,
            event_id=new_event_id(),
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
            subject=grant.subject,
            issuer=approval.evidence.issuer if approval is not None else grant.issuer,
            control_implementation=grant.control_implementation,
            approval_source_event_id=(
                approval.evidence.source_event_id if approval is not None else grant.approval_source_event_id
            ),
            action=action,
            reason_code=safe_reason,
            recorded_at=self._clock.now_utc(),
            previous_entry_sha256=self._durable_head,
            entry_sha256="0" * 64,
        )
        entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
        return self._append(entry)  # type: ignore[return-value]

    def operation_history(self, operation_key: str) -> tuple[OperationAuditEntry, ...]:
        self.verify_chain()
        return tuple(entry for entry in self.operation_entries if entry.operation_key == operation_key)

    def append_operation(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant | None,
        outcome: OperationAuditOutcome,
        reason_code: ProtectedAuditReason | str,
        capability: ProtectedAuthorizationCapability | None = None,
        result: ProtectedOperationResult | None = None,
    ) -> OperationAuditEntry:
        try:
            safe_reason = ProtectedAuditReason(reason_code)
        except ValueError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None
        history = self.operation_history(request.operation_key)
        if not self._valid_operation_transition(history, outcome):
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
        if history and self._operation_binding(history[0]) != self._request_binding(request, grant):
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        if (outcome is OperationAuditOutcome.SUCCEEDED) != (result is not None):
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        entry = OperationAuditEntry(
            event_kind=ProtectedAuditEventKind.OPERATION,
            sequence=self._durable_sequence + 1,
            event_id=new_event_id(),
            operation_key=request.operation_key,
            request_id=request.request_id,
            principal=request.principal,
            protected_action=request.action,
            target_ref=request.target_ref,
            grant_id=grant.grant_id if grant else None,
            grant_revision=grant.revision if grant else None,
            dataset_state_revision=request.dataset.state_revision,
            protected_artifact_sha256=request.dataset.protected_artifact_sha256,
            capability_nonce=capability.nonce if capability else None,
            result_ref=result.result_ref if result else None,
            outcome=outcome,
            reason_code=safe_reason,
            recorded_at=self._clock.now_utc(),
            previous_entry_sha256=self._durable_head,
            entry_sha256="0" * 64,
        )
        entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
        return self._append(entry)  # type: ignore[return-value]

    @staticmethod
    def _request_binding(
        request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant | None
    ) -> tuple[object, ...]:
        return (
            request.request_id,
            request.principal,
            request.action,
            request.target_ref,
            grant.grant_id if grant else None,
            grant.revision if grant else None,
            request.dataset.state_revision,
            request.dataset.protected_artifact_sha256,
        )

    @staticmethod
    def _operation_binding(entry: OperationAuditEntry) -> tuple[object, ...]:
        return (
            entry.request_id,
            entry.principal,
            entry.protected_action,
            entry.target_ref,
            entry.grant_id,
            entry.grant_revision,
            entry.dataset_state_revision,
            entry.protected_artifact_sha256,
        )

    @staticmethod
    def _valid_operation_transition(history: tuple[OperationAuditEntry, ...], outcome: OperationAuditOutcome) -> bool:
        if not history:
            return outcome in {OperationAuditOutcome.DENIED, OperationAuditOutcome.INTENT}
        return (
            len(history) == 1
            and history[0].outcome is OperationAuditOutcome.INTENT
            and outcome
            in {
                OperationAuditOutcome.SUCCEEDED,
                OperationAuditOutcome.UNKNOWN,
                OperationAuditOutcome.DENIED,
            }
        )

    def verify_chain(self) -> None:
        if (
            len(self.entries) != self._durable_sequence
            or (self.entries[-1].entry_sha256 if self.entries else None) != self._durable_head
        ):
            raise ProtectedSecurityError("AUDIT_TAIL_TRUNCATED")
        previous: str | None = None
        event_ids: set[str] = set()
        last_recorded_at: datetime | None = None
        for sequence, entry in enumerate(self.entries, start=1):
            if entry.sequence != sequence or entry.previous_entry_sha256 != previous:
                raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
            if audit_entry_sha256(entry) != entry.entry_sha256:
                raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
            if entry.event_id in event_ids or (last_recorded_at and entry.recorded_at < last_recorded_at):
                raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
            event_ids.add(entry.event_id)
            last_recorded_at = entry.recorded_at
            previous = entry.entry_sha256


class InMemoryAuthorizationLedger:
    def __init__(
        self,
        journal: InMemoryProtectedAuditJournal,
        clock: FixedTrustedClock,
        verifier: InMemoryApprovalEvidenceVerifier,
    ) -> None:
        self._journal = journal
        self._clock = clock
        self._grants: dict[str, ProtectedAuthorizationGrant] = {}
        self._revoked: set[str] = set()
        self._revisions: dict[str, int] = {}
        self._datasets: dict[tuple[str, str], ProtectedDatasetBinding] = {}
        self._verifier = verifier
        self.transaction_lock = asyncio.Lock()

    def register_dataset(self, dataset: ProtectedDatasetBinding) -> None:
        key = (dataset.dataset_id, dataset.dataset_version)
        existing = self._datasets.get(key)
        if existing is not None and existing != dataset:
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        self._datasets[key] = dataset

    def require_dataset(self, request: ProtectedOperationRequest) -> ProtectedDatasetBinding:
        dataset = self._datasets.get((request.dataset.dataset_id, request.dataset.dataset_version))
        if dataset is None or dataset != request.dataset:
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        return dataset

    async def transition_dataset(
        self,
        expected: ProtectedDatasetBinding,
        updated: ProtectedDatasetBinding,
    ) -> None:
        if (expected.dataset_id, expected.dataset_version) != (updated.dataset_id, updated.dataset_version):
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        if updated.state_revision != expected.state_revision + 1:
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        async with self.transaction_lock:
            key = (expected.dataset_id, expected.dataset_version)
            if self._datasets.get(key) != expected:
                raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
            self._datasets[key] = updated

    def grant(self, grant: ProtectedAuthorizationGrant) -> None:
        approval = self._verifier.verify(grant)
        if approval.evidence.source_event_id != grant.approval_source_event_id:
            raise ProtectedSecurityError("APPROVAL_EVIDENCE_MISMATCH")
        if approval.action is not AuthorizationAuditAction.GRANT:
            raise ProtectedSecurityError("APPROVAL_ACTION_MISMATCH")
        if approval.grant_sha256 != authorization_grant_sha256(grant):
            raise ProtectedSecurityError("APPROVAL_GRANT_BINDING_MISMATCH")
        self._journal.append_authorization(
            grant,
            AuthorizationAuditAction.GRANT,
            "APPROVAL_VERIFIED",
            approval=approval,
        )
        self._grants[grant.grant_id] = grant
        self._revisions[grant.grant_id] = grant.revision

    async def revoke(self, grant_id: str, source_event_id: str, expected_raw_sha256: str) -> None:
        async with self.transaction_lock:
            grant = self.require_current(grant_id)
            approval = self._verifier.verify_revoke(grant, source_event_id, expected_raw_sha256)
            self._journal.append_authorization(
                grant,
                AuthorizationAuditAction.REVOKE,
                "REVOKED",
                approval=approval,
            )
            self._revisions[grant_id] += 1
            self._revoked.add(grant_id)

    def current(self, grant_id: str) -> ProtectedAuthorizationGrant | None:
        grant = self._grants.get(grant_id)
        return None if grant_id in self._revoked else grant

    def current_revision(self, grant_id: str) -> int | None:
        return self._revisions.get(grant_id)

    def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant:
        grant = self.current(grant_id)
        if grant is None:
            raise ProtectedSecurityError("AUTHORIZATION_REVOKED")
        if self._clock.now_utc() >= grant.expires_at:
            self._journal.append_authorization(grant, AuthorizationAuditAction.EXPIRE, "EXPIRED")
            self._revoked.add(grant_id)
            self._revisions[grant_id] += 1
            raise ProtectedSecurityError("AUTHORIZATION_EXPIRED")
        return grant

    def find_for(self, request: ProtectedOperationRequest) -> ProtectedAuthorizationGrant | None:
        candidates = [
            grant
            for grant_id, grant in self._grants.items()
            if grant_id not in self._revoked
            and grant.subject == request.principal
            and grant.dataset_id == request.dataset.dataset_id
            and grant.dataset_version == request.dataset.dataset_version
            and grant.manifest_sha256 == request.dataset.manifest_sha256
            and grant.protected_artifact_sha256 == request.dataset.protected_artifact_sha256
            and grant.hmac_key_version == request.dataset.hmac_key_version
            and request.action in grant.actions
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.revision)

    def simulate_concurrent_revocation(self, grant_id: str) -> None:
        self._revoked.add(grant_id)


class _GuardSession:
    def __init__(
        self,
        ledger: InMemoryAuthorizationLedger,
        clock: FixedTrustedClock,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant,
        *,
        revoke_before_consume: bool,
    ) -> None:
        self._ledger = ledger
        self._clock = clock
        self._request = request
        self._grant = grant
        self._revoke_before_consume = revoke_before_consume
        self._consumed: set[str] = set()

    def issue_capability(
        self, request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant
    ) -> ProtectedAuthorizationCapability:
        if request != self._request or grant != self._grant:
            raise ProtectedSecurityError("GUARD_BINDING_MISMATCH")
        self._ledger.require_current(grant.grant_id)
        return ProtectedAuthorizationCapability(
            request_id=request.request_id,
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
            dataset_state_revision=request.dataset.state_revision,
            protected_artifact_sha256=request.dataset.protected_artifact_sha256,
            action=request.action,
            target_ref=request.target_ref,
            nonce=str(uuid4()),
            expires_at=self._clock.now_utc() + timedelta(seconds=30),
        )

    def consume(self, capability: ProtectedAuthorizationCapability) -> None:
        if self._revoke_before_consume:
            self._ledger.simulate_concurrent_revocation(self._grant.grant_id)
        self._ledger.require_current(self._grant.grant_id)
        self._ledger.require_dataset(self._request)
        if capability.nonce in self._consumed:
            raise ProtectedSecurityError("CAPABILITY_ALREADY_CONSUMED")
        if self._clock.now_utc() >= capability.expires_at:
            raise ProtectedSecurityError("CAPABILITY_EXPIRED")
        if (
            capability.request_id != self._request.request_id
            or capability.grant_id != self._grant.grant_id
            or capability.grant_revision != self._grant.revision
            or capability.dataset_state_revision != self._request.dataset.state_revision
            or capability.protected_artifact_sha256 != self._request.dataset.protected_artifact_sha256
            or capability.action != self._request.action
            or capability.target_ref != self._request.target_ref
        ):
            raise ProtectedSecurityError("CAPABILITY_BINDING_MISMATCH")
        self._consumed.add(capability.nonce)


class InMemoryAuthorizationGuard:
    def __init__(self, ledger: InMemoryAuthorizationLedger, clock: FixedTrustedClock) -> None:
        self._ledger = ledger
        self._clock = clock
        self.revoke_before_consume = False

    @asynccontextmanager
    async def hold(
        self, request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant
    ) -> AsyncIterator[_GuardSession]:
        async with self._ledger.transaction_lock:
            current = self._ledger.require_current(grant.grant_id)
            if current != grant:
                raise ProtectedSecurityError("AUTHORIZATION_REVISION_MISMATCH")
            yield _GuardSession(
                self._ledger,
                self._clock,
                request,
                grant,
                revoke_before_consume=self.revoke_before_consume,
            )


class SyntheticProtectedOperation:
    def __init__(self) -> None:
        self.call_count = 0
        self.fail_after_side_effect = False
        self._results: dict[str, ProtectedOperationResult] = {}
        self.pause_before_return: asyncio.Event | None = None
        self.resume: asyncio.Event | None = None

    async def execute(
        self, request: ProtectedOperationRequest, capability: ProtectedAuthorizationCapability
    ) -> ProtectedOperationResult:
        prior = self._results.get(request.operation_key)
        if prior is not None:
            return prior
        self.call_count += 1
        result = ProtectedOperationResult(
            result_ref=OpaqueLogicalRef(namespace=OpaqueRefNamespace.RUN_RESULT, value=str(uuid4())),
            reason_code="PROTECTED_OPERATION_SUCCEEDED",
        )
        self._results[request.operation_key] = result
        if self.fail_after_side_effect:
            raise RuntimeError("synthetic uncertain operation")
        if self.pause_before_return is not None:
            self.pause_before_return.set()
            if self.resume is not None:
                await self.resume.wait()
        return result

    def observe(self, operation_key: str) -> ProtectedOperationResult | None:
        return self._results.get(operation_key)


__all__ = [
    "FixedTrustedClock",
    "InMemoryApprovalEvidenceVerifier",
    "InMemoryAuthorizationGuard",
    "InMemoryAuthorizationLedger",
    "InMemoryProtectedAuditJournal",
    "SyntheticProtectedOperation",
]
