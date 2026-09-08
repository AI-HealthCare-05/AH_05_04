"""Internal synthetic execution model; no production adapters or entry points are registered."""

import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol
from uuid import uuid4

from ai_worker.tasks.rag.source_cleanup.preflight import (
    ApprovalEvidence,
    ApprovalVerifier,
    BatchTarget,
    ReviewBatch,
    _approval_matches,
    _utc,
)
from ai_worker.tasks.rag.source_cleanup.survey import ReferenceReader, SurveyDecision, classify

Outcome = Literal["INTENT", "DELETED", "FAILED", "UNKNOWN", "BLOCKED"]


@dataclass(frozen=True)
class AuditEntry:
    batch_hash: str
    object_ref: str
    attempt_id: str
    event: Outcome
    reason: str
    checksum: str
    artifact_kind: str
    policy_version: str
    receipt_id: str
    pm_actor: str
    db_security_actor: str
    executor: str
    occurred_at: str
    references_verified: bool = True


class AuditJournal(Protocol):
    def history(self, batch_hash: str, object_ref: str) -> tuple[AuditEntry, ...]: ...

    def append(self, entry: AuditEntry) -> None:
        """Return only after durable append; raise on uncertain writes. Never update/delete history."""
        ...


class DeletionSession(Protocol):
    @property
    def references(self) -> ReferenceReader: ...

    @property
    def audit(self) -> AuditJournal: ...

    async def assert_held(self) -> None: ...

    async def observe(self, target: BatchTarget) -> BatchTarget | None: ...

    async def delete(self, target: BatchTarget) -> None:
        """Recheck bytes/generation and unlink under the still-held guard; raise on uncertainty."""
        ...


class DeletionGuard(Protocol):
    def acquire(self, batch: ReviewBatch) -> AbstractAsyncContextManager[DeletionSession]: ...


@dataclass(frozen=True)
class ExecutionItem:
    object_ref: str
    outcome: Outcome
    reason: str


@dataclass(frozen=True)
class ExecutionResult:
    items: tuple[ExecutionItem, ...]
    complete: bool
    reason: str


def _object_ref(batch: ReviewBatch, target: BatchTarget) -> str:
    return hashlib.sha256(f"{batch.digest()}:{target.observation.object_key}".encode()).hexdigest()


async def _verify(
    batch: ReviewBatch, executor: str, clock: Callable[[], datetime], approvals: ApprovalVerifier
) -> ApprovalEvidence:
    receipt = await approvals.verify(batch_digest=batch.digest(), executor=executor)
    if not _approval_matches(receipt, batch.digest(), batch, executor, clock()) or receipt is None:
        raise ValueError("Approval unavailable")
    return receipt


async def _check_target(session: DeletionSession, batch: ReviewBatch, target: BatchTarget, now: datetime) -> None:
    await session.assert_held()
    if await session.observe(target) != target:
        raise ValueError("Object changed")
    refs = await session.references.inspect_references(
        storage_backend=batch.scope.storage_backend, object_key=target.observation.object_key
    )
    if classify(target.observation, refs, batch.scope, now)[0] is not SurveyDecision.REVIEW_CANDIDATE:
        raise ValueError("Reference or retention check failed")


async def _attempt(
    session: DeletionSession,
    batch: ReviewBatch,
    target: BatchTarget,
    executor: str,
    approvals: ApprovalVerifier,
    clock: Callable[[], datetime],
) -> ExecutionItem:
    ref = _object_ref(batch, target)
    receipt = await _verify(batch, executor, clock, approvals)
    await _check_target(session, batch, target, clock())
    attempt_id = str(uuid4())

    def record(event: Outcome, reason: str) -> None:
        session.audit.append(
            AuditEntry(
                batch.digest(),
                ref,
                attempt_id,
                event,
                reason,
                target.observation.checksum,
                target.artifact_kind,
                batch.scope.policy_version,
                receipt.receipt_id,
                receipt.pm_actor,
                receipt.db_security_actor,
                executor,
                _utc(clock()),
            )
        )

    # Failure here must never call delete. A partially persisted INTENT remains recoverable.
    record("INTENT", "FINAL_CHECKS_PASSED")
    try:
        # Audit I/O may be slow. Recheck approval, references and bytes immediately before delete.
        receipt = await _verify(batch, executor, clock, approvals)
        await _check_target(session, batch, target, clock())
        await session.assert_held()
    except Exception:
        record("BLOCKED", "FINAL_RECHECK_FAILED")
        return ExecutionItem(ref, "BLOCKED", "FINAL_RECHECK_FAILED")
    try:
        await session.delete(target)
    except Exception:
        # Even an exception can follow successful unlink: never assume the file survived.
        record("UNKNOWN", "DELETE_RESULT_UNKNOWN")
        return ExecutionItem(ref, "UNKNOWN", "DELETE_RESULT_UNKNOWN")
    record("DELETED", "DELETE_CONFIRMED")
    return ExecutionItem(ref, "DELETED", "DELETE_CONFIRMED")


async def _execute_target(
    session: DeletionSession,
    batch: ReviewBatch,
    target: BatchTarget,
    executor: str,
    approvals: ApprovalVerifier,
    clock: Callable[[], datetime],
    retry: bool,
    max_attempts: int,
) -> ExecutionItem:
    ref = _object_ref(batch, target)
    history = session.audit.history(batch.digest(), ref)
    if history:
        await session.assert_held()
        observed = await session.observe(target)
        if history[-1].event == "DELETED":
            if observed is None:
                return ExecutionItem(ref, "DELETED", "ALREADY_RECORDED")
            return ExecutionItem(ref, "BLOCKED", "RECREATED_AFTER_SUCCESS")
        if observed is None:
            # Missing after INTENT/UNKNOWN cannot be attributed to this deletion attempt.
            if history[-1].event == "INTENT":
                session.audit.append(
                    replace(
                        history[-1],
                        event="UNKNOWN",
                        reason="MISSING_REQUIRES_RECONCILIATION",
                        occurred_at=_utc(clock()),
                    )
                )
            return ExecutionItem(ref, "UNKNOWN", "MISSING_REQUIRES_RECONCILIATION")
        if not retry or sum(entry.event == "INTENT" for entry in history) >= max_attempts:
            return ExecutionItem(ref, "BLOCKED", "RETRY_REQUIRES_REVIEW")
    return await _attempt(session, batch, target, executor, approvals, clock)


async def execute_synthetic_batch(
    *,
    batch: ReviewBatch,
    executor: str,
    clock: Callable[[], datetime],
    approvals: ApprovalVerifier,
    guard: DeletionGuard,
    retry: bool = False,
    max_attempts: int = 1,
) -> ExecutionResult:
    """One bounded manual call. Never use the earlier dry-run result as deletion authorization."""
    items: list[ExecutionItem] = []
    completed = False
    try:
        batch.digest()  # Reject non-synthetic environment, malformed scope and target metadata.
        _utc(clock())
        if not executor.strip() or type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            return ExecutionResult((), False, "EXECUTION_INPUT_INVALID")
        await _verify(batch, executor, clock, approvals)
        async with guard.acquire(batch) as session:
            for target in sorted(batch.targets, key=lambda item: item.observation.object_key):
                items.append(
                    await _execute_target(
                        session,
                        batch,
                        target,
                        executor,
                        approvals,
                        clock,
                        retry,
                        max_attempts,
                    )
                )
            await session.assert_held()
            completed = True
    except Exception:
        # Includes audit failure after successful deletion. The durable INTENT drives recovery.
        return ExecutionResult(tuple(items), False, "EXECUTION_OR_AUDIT_UNAVAILABLE")
    complete = completed and len(items) == len(batch.targets) and all(i.outcome == "DELETED" for i in items)
    return ExecutionResult(tuple(items), complete, "COMPLETED" if complete else "REVIEW_REQUIRED")
