from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from ai_worker.tasks.rag.source_cleanup.preflight import (
    ApprovalEvidence,
    BatchTarget,
    GuardedInspection,
    LockedObservation,
    ReviewBatch,
    check_batch,
)
from ai_worker.tests.rag.source_cleanup.test_survey import NOW, OBJ, REFS, SCOPE

BATCH = ReviewBatch(SCOPE, (BatchTarget(OBJ, "synthetic-generation-1"),))


def receipt(batch=BATCH):
    return ApprovalEvidence(
        batch.digest(),
        SCOPE.policy_version,
        "synthetic-receipt",
        "synthetic-pm",
        "synthetic-db-reviewer",
        "synthetic-executor",
        NOW - timedelta(hours=1),
        NOW + timedelta(hours=1),
    )


class SyntheticGuard:
    def __init__(self):
        self.entered = False
        self.exited = False
        self.held = False
        self.fail_exit = False
        self.inspection = AsyncMock(spec=GuardedInspection)
        self.inspection.observe.return_value = LockedObservation(BATCH.scope, BATCH.targets, True)
        self.inspection.references = AsyncMock()
        self.inspection.references.inspect_references.return_value = REFS
        self.inspection.assert_held.side_effect = self.assert_held

    async def assert_held(self):
        if not self.held:
            raise RuntimeError("synthetic-lost-lock")

    @asynccontextmanager
    async def acquire(self, batch):
        self.entered = True
        self.held = True
        try:
            yield self.inspection
        finally:
            self.held = False
            self.exited = True
            if self.fail_exit:
                raise RuntimeError("synthetic-private-lock-error")


async def run(*, batch=BATCH, approval=None, guard=None):
    verifier = AsyncMock()
    verifier.verify.return_value = receipt() if approval is None else approval
    guard = guard or SyntheticGuard()
    result = await check_batch(
        batch=batch, executor="synthetic-executor", clock=lambda: NOW, approvals=verifier, guard=guard
    )
    return result, verifier, guard


async def test_success_is_dry_run_and_guard_covers_all_reads():
    guard = SyntheticGuard()

    async def references(**kwargs):
        assert guard.held
        return REFS

    guard.inspection.references.inspect_references.side_effect = references
    result, approvals, guard = await run(guard=guard)
    assert result.checks_passed and result.reason == "DRY_RUN_CHECKS_PASSED"
    assert guard.exited and not guard.held
    assert approvals.verify.await_count == 2
    assert guard.inspection.assert_held.await_count == 2


@pytest.mark.parametrize(
    "change",
    [
        {"batch_digest": "0" * 64},
        {"policy_version": "other"},
        {"receipt_id": ""},
        {"pm_actor": ""},
        {"db_security_actor": ""},
        {"executor": "other"},
        {"valid_from": NOW + timedelta(seconds=1)},
        {"expires_at": NOW},
        {"expires_at": NOW.replace(tzinfo=None)},
    ],
)
async def test_invalid_approval_never_acquires_guard(change):
    result, _, guard = await run(approval=replace(receipt(), **change))
    assert not result.checks_passed
    assert not guard.entered


@pytest.mark.parametrize("port", ["approvals", "guard"])
async def test_missing_trusted_port_blocks(port):
    kwargs = {"approvals": AsyncMock(), "guard": SyntheticGuard()}
    kwargs[port] = None
    result = await check_batch(batch=BATCH, executor="synthetic-executor", clock=lambda: NOW, **kwargs)
    assert not result.checks_passed and result.reason == "TRUSTED_PORT_UNAVAILABLE"


@pytest.mark.parametrize(
    "change",
    [
        {"checksum": "b" * 64},
        {"byte_size": 99},
        {"created_at": NOW - timedelta(days=32)},
    ],
)
async def test_replaced_object_bytes_or_creation_rejected(change):
    guard = SyntheticGuard()
    target = replace(BATCH.targets[0], observation=replace(OBJ, **change))
    guard.inspection.observe.return_value = LockedObservation(SCOPE, (target,), True)
    result, _, guard = await run(guard=guard)
    assert result.reason == "OBJECT_OR_SCOPE_CHANGED"
    assert guard.exited
    guard.inspection.references.inspect_references.assert_not_called()


@pytest.mark.parametrize("change", ["generation", "namespace", "database", "incomplete", "missing"])
async def test_scope_identity_generation_or_inventory_change_blocks(change):
    observed = LockedObservation(SCOPE, BATCH.targets, True)
    if change == "generation":
        observed = replace(observed, targets=(replace(BATCH.targets[0], generation="recreated"),))
    elif change == "namespace":
        observed = replace(observed, scope=replace(SCOPE, namespace="other-root"))
    elif change == "database":
        observed = replace(observed, scope=replace(SCOPE, database_id="other-db"))
    elif change == "incomplete":
        observed = replace(observed, complete=False)
    else:
        observed = replace(observed, targets=())
    guard = SyntheticGuard()
    guard.inspection.observe.return_value = observed
    result, _, _ = await run(guard=guard)
    assert not result.checks_passed


@pytest.mark.parametrize(
    "change", [{"direct_count": 1}, {"downstream_count": 1}, {"scope_complete": False}, {"acquisition_idle": False}]
)
async def test_new_reference_or_active_writer_blocks(change):
    guard = SyntheticGuard()
    guard.inspection.references.inspect_references.return_value = replace(REFS, **change)
    result, _, guard = await run(guard=guard)
    assert result.reason == "FINAL_REFERENCE_CHECK_FAILED"
    assert guard.exited


async def test_revocation_after_read_blocks():
    approvals = AsyncMock()
    approvals.verify.side_effect = [receipt(), None]
    result = await check_batch(
        batch=BATCH, executor="synthetic-executor", clock=lambda: NOW, approvals=approvals, guard=SyntheticGuard()
    )
    assert not result.checks_passed and result.reason == "APPROVAL_INVALID"


async def test_expiry_while_waiting_for_reads_blocks():
    approvals = AsyncMock()
    approvals.verify.return_value = receipt()
    ticks = iter([NOW, NOW, NOW, NOW + timedelta(hours=1)])
    result = await check_batch(
        batch=BATCH,
        executor="synthetic-executor",
        clock=lambda: next(ticks),
        approvals=approvals,
        guard=SyntheticGuard(),
    )
    assert not result.checks_passed


@pytest.mark.parametrize("failure", ["lost", "read", "exit", "approval"])
async def test_guard_and_provider_failures_are_closed_and_redacted(failure):
    guard = SyntheticGuard()
    approvals = AsyncMock()
    approvals.verify.return_value = receipt()
    if failure == "lost":
        guard.inspection.assert_held.side_effect = [None, RuntimeError("private-secret")]
    elif failure == "read":
        guard.inspection.observe.side_effect = RuntimeError("private-secret")
    elif failure == "exit":
        guard.fail_exit = True
    else:
        approvals.verify.side_effect = RuntimeError("private-secret")
    result = await check_batch(
        batch=BATCH, executor="synthetic-executor", clock=lambda: NOW, approvals=approvals, guard=guard
    )
    assert not result.checks_passed
    assert "private" not in repr(result)


def test_batch_hash_binds_scope_policy_targets_and_generation():
    target2 = BatchTarget(replace(OBJ, object_key="other-key"), "other-generation")
    forward = replace(BATCH, targets=(*BATCH.targets, target2))
    reverse = replace(BATCH, targets=(target2, *BATCH.targets))
    assert forward.digest() == reverse.digest()
    for changed in [
        replace(BATCH, scope=replace(SCOPE, database_id="other-db")),
        replace(BATCH, scope=replace(SCOPE, namespace="other-root")),
        replace(BATCH, targets=(replace(BATCH.targets[0], generation="new"),)),
        forward,
    ]:
        assert changed.digest() != BATCH.digest()


@pytest.mark.parametrize(
    "batch",
    [
        replace(BATCH, environment="PRODUCTION"),
        replace(BATCH, targets=()),
        replace(BATCH, targets=BATCH.targets * 2),
        replace(BATCH, scope=replace(SCOPE, policy_version="unknown")),
    ],
)
async def test_invalid_batch_never_asks_for_approval(batch):
    approvals = AsyncMock()
    result = await check_batch(
        batch=batch, executor="synthetic-executor", clock=lambda: NOW, approvals=approvals, guard=SyntheticGuard()
    )
    assert not result.checks_passed
    approvals.verify.assert_not_called()


async def test_guard_suppressing_read_error_cannot_report_success():
    class SuppressingGuard:
        @asynccontextmanager
        async def acquire(self, batch):
            inspection = AsyncMock(spec=GuardedInspection)
            inspection.observe.side_effect = RuntimeError("private-secret")
            try:
                yield inspection
            except RuntimeError:
                pass

    approvals = AsyncMock()
    approvals.verify.return_value = receipt()
    result = await check_batch(
        batch=BATCH,
        executor="synthetic-executor",
        clock=lambda: NOW,
        approvals=approvals,
        guard=SuppressingGuard(),
    )
    assert not result.checks_passed
    assert result.reason == "GUARDED_CHECK_UNAVAILABLE"
