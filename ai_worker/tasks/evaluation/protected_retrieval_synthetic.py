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
    ProtectedAuditEntry,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationCapability,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedDatasetState,
    ProtectedOperationRequest,
    ProtectedOperationResult,
    ProtectedSecurityError,
    VerifiedAuthorizationApproval,
    _operation_lifecycle_terminal,
    audit_entry_sha256,
    authorization_grant_sha256,
    new_event_id,
)
from ai_worker.tasks.evaluation.protected_retrieval_control import verify_authorization_approval


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
        return verify_authorization_approval(grant, trusted, action, expected_raw_sha256)


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
            effective_revision=(grant.revision if action is AuthorizationAuditAction.GRANT else grant.revision + 1),
            subject=grant.subject,
            issuer=approval.evidence.issuer if approval is not None else grant.issuer,
            dataset_id=grant.dataset_id,
            dataset_version=grant.dataset_version,
            manifest_sha256=grant.manifest_sha256,
            protected_artifact_sha256=grant.protected_artifact_sha256,
            hmac_key_version=grant.hmac_key_version,
            actions=grant.actions,
            control_implementation=grant.control_implementation,
            approval_source_event_id=(
                approval.evidence.source_event_id if approval is not None else grant.approval_source_event_id
            ),
            approval_source_raw_sha256=(
                approval.evidence.canonical_raw_sha256 if approval is not None else grant.approval_source_raw_sha256
            ),
            valid_from=grant.valid_from,
            expires_at=grant.expires_at,
            action=action,
            reason_code=safe_reason,
            recorded_at=self._clock.now_utc(),
            previous_entry_sha256=self._durable_head,
            entry_sha256="0" * 64,
        )
        entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
        return self._append(entry)  # type: ignore[return-value]

    async def operation_history(self, request: ProtectedOperationRequest) -> tuple[OperationAuditEntry, ...]:
        self.verify_chain()
        return tuple(
            entry
            for entry in self.operation_entries
            if entry.operation_key == request.operation_key
            and entry.principal == request.principal
            and entry.protected_action is request.action
            and entry.target_ref == request.target_ref
            and entry.dataset_id == request.dataset.dataset_id
            and entry.dataset_version == request.dataset.dataset_version
            and entry.manifest_sha256 == request.dataset.manifest_sha256
            and entry.protected_artifact_sha256 == request.dataset.protected_artifact_sha256
            and entry.hmac_key_version == request.dataset.hmac_key_version
        )

    async def append_operation(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant | None,
        outcome: OperationAuditOutcome,
        reason_code: ProtectedAuditReason | str,
        capability: ProtectedAuthorizationCapability | None = None,
        result: ProtectedOperationResult | None = None,
        *,
        closes_intent: bool = False,
    ) -> OperationAuditEntry:
        try:
            safe_reason = ProtectedAuditReason(reason_code)
        except ValueError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None
        history = await self.operation_history(request)
        if not self._valid_operation_transition(history, outcome, closes_intent=closes_intent):
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")
        lifecycle_terminal = _operation_lifecycle_terminal(history)
        if (
            lifecycle_terminal is not None
            and lifecycle_terminal.outcome is OperationAuditOutcome.INTENT
            and (
                outcome in {OperationAuditOutcome.SUCCEEDED, OperationAuditOutcome.UNKNOWN}
                or (outcome is OperationAuditOutcome.DENIED and closes_intent)
            )
            and self._operation_binding(lifecycle_terminal) != self._request_binding(request, grant)
        ):
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
            dataset_id=request.dataset.dataset_id,
            dataset_version=request.dataset.dataset_version,
            manifest_sha256=request.dataset.manifest_sha256,
            hmac_key_version=request.dataset.hmac_key_version,
            grant_id=grant.grant_id if grant else None,
            grant_revision=grant.revision if grant else None,
            dataset_state_revision=request.dataset.state_revision,
            protected_artifact_sha256=request.dataset.protected_artifact_sha256,
            capability_nonce=capability.nonce if capability else None,
            result_ref=result.result_ref if result else None,
            closes_intent=closes_intent,
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
            request.dataset.dataset_id,
            request.dataset.dataset_version,
            request.dataset.manifest_sha256,
            request.dataset.hmac_key_version,
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
            entry.dataset_id,
            entry.dataset_version,
            entry.manifest_sha256,
            entry.hmac_key_version,
            entry.grant_id,
            entry.grant_revision,
            entry.dataset_state_revision,
            entry.protected_artifact_sha256,
        )

    @staticmethod
    def _valid_operation_transition(
        history: tuple[OperationAuditEntry, ...],
        outcome: OperationAuditOutcome,
        *,
        closes_intent: bool,
    ) -> bool:
        terminal = _operation_lifecycle_terminal(history)
        if outcome is OperationAuditOutcome.DENIED:
            return not closes_intent or (terminal is not None and terminal.outcome is OperationAuditOutcome.INTENT)
        if terminal is None or terminal.outcome is OperationAuditOutcome.DENIED:
            return outcome is OperationAuditOutcome.INTENT
        return terminal.outcome is OperationAuditOutcome.INTENT and outcome in {
            OperationAuditOutcome.SUCCEEDED,
            OperationAuditOutcome.UNKNOWN,
        }

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

    async def require_dataset(self, request: ProtectedOperationRequest) -> ProtectedDatasetBinding:
        return self._require_dataset(request)

    def _require_dataset(self, request: ProtectedOperationRequest) -> ProtectedDatasetBinding:
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
        allowed_transitions = {
            ProtectedDatasetState.ACCESS_AUTHORIZED: ProtectedDatasetState.AUTHORING,
            ProtectedDatasetState.AUTHORING: ProtectedDatasetState.REVIEW_READY,
            ProtectedDatasetState.REVIEW_READY: ProtectedDatasetState.FROZEN,
        }
        if (
            updated.state_revision != expected.state_revision + 1
            or allowed_transitions.get(expected.state) is not updated.state
        ):
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        async with self.transaction_lock:
            key = (expected.dataset_id, expected.dataset_version)
            if self._datasets.get(key) != expected:
                raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
            self._datasets[key] = updated

    async def grant(self, grant: ProtectedAuthorizationGrant) -> None:
        async with self.transaction_lock:
            self._grant(grant)

    def _grant(self, grant: ProtectedAuthorizationGrant) -> None:
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
            grant = self._require_current(grant_id)
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

    async def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant:
        async with self.transaction_lock:
            return self._require_current(grant_id)

    def _require_current(self, grant_id: str) -> ProtectedAuthorizationGrant:
        grant = self.current(grant_id)
        if grant is None:
            raise ProtectedSecurityError("AUTHORIZATION_REVOKED")
        if self._clock.now_utc() >= grant.expires_at:
            self._journal.append_authorization(grant, AuthorizationAuditAction.EXPIRE, "EXPIRED")
            self._revoked.add(grant_id)
            self._revisions[grant_id] += 1
            raise ProtectedSecurityError("AUTHORIZATION_EXPIRED")
        return grant

    async def find_for(self, request: ProtectedOperationRequest) -> ProtectedAuthorizationGrant | None:
        async with self.transaction_lock:
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
            now = self._clock.now_utc()
            for expired in (grant for grant in candidates if now >= grant.expires_at):
                self._journal.append_authorization(expired, AuthorizationAuditAction.EXPIRE, "EXPIRED")
                self._revoked.add(expired.grant_id)
                self._revisions[expired.grant_id] += 1
            unexpired_candidates = [grant for grant in candidates if now < grant.expires_at]
            if not unexpired_candidates:
                return None
            active_candidates = [grant for grant in unexpired_candidates if grant.valid_from <= now]
            return max(active_candidates or unexpired_candidates, key=lambda item: item.revision)

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

    async def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant:
        return self._ledger._require_current(grant_id)

    async def issue_capability(
        self, request: ProtectedOperationRequest, grant: ProtectedAuthorizationGrant
    ) -> ProtectedAuthorizationCapability:
        if request != self._request or grant != self._grant:
            raise ProtectedSecurityError("GUARD_BINDING_MISMATCH")
        self._ledger._require_current(grant.grant_id)
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

    async def consume(self, capability: ProtectedAuthorizationCapability) -> None:
        if self._revoke_before_consume:
            self._ledger.simulate_concurrent_revocation(self._grant.grant_id)
        self._ledger._require_current(self._grant.grant_id)
        self._ledger._require_dataset(self._request)
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
            current = self._ledger._require_current(grant.grant_id)
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
        self._results: dict[tuple[str, ...], ProtectedOperationResult] = {}
        self.pause_before_return: asyncio.Event | None = None
        self.resume: asyncio.Event | None = None

    async def execute(
        self, request: ProtectedOperationRequest, capability: ProtectedAuthorizationCapability
    ) -> ProtectedOperationResult:
        operation_scope = self._operation_scope(request)
        prior = self._results.get(operation_scope)
        if prior is not None:
            return prior
        self.call_count += 1
        result = ProtectedOperationResult(
            result_ref=OpaqueLogicalRef(namespace=OpaqueRefNamespace.RUN_RESULT, value=str(uuid4())),
            reason_code="PROTECTED_OPERATION_SUCCEEDED",
        )
        self._results[operation_scope] = result
        if self.fail_after_side_effect:
            raise RuntimeError("synthetic uncertain operation")
        if self.pause_before_return is not None:
            self.pause_before_return.set()
            if self.resume is not None:
                await self.resume.wait()
        return result

    def observe(self, request: ProtectedOperationRequest) -> ProtectedOperationResult | None:
        return self._results.get(self._operation_scope(request))

    @staticmethod
    def _operation_scope(request: ProtectedOperationRequest) -> tuple[str, ...]:
        return (
            request.operation_key,
            request.principal.actor.actor_id,
            request.principal.actor.namespace,
            request.principal.role,
            request.action,
            request.target_ref.namespace,
            request.target_ref.value,
            request.dataset.dataset_id,
            request.dataset.dataset_version,
            request.dataset.manifest_sha256,
            request.dataset.protected_artifact_sha256,
            request.dataset.hmac_key_version,
        )


__all__ = [
    "FixedTrustedClock",
    "InMemoryApprovalEvidenceVerifier",
    "InMemoryAuthorizationGuard",
    "InMemoryAuthorizationLedger",
    "InMemoryProtectedAuditJournal",
    "SyntheticProtectedOperation",
]
