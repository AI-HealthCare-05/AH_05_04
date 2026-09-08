"""고정된 배치의 승인·최종 검사를 수행합니다. 결과는 삭제 허가 토큰이 아닙니다."""

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from ai_worker.tasks.rag.source_cleanup.survey import (
    Inventory,
    InventoryReader,
    ObjectObservation,
    ReferenceReader,
    SurveyDecision,
    SurveyScope,
    survey_candidates,
)


@dataclass(frozen=True)
class BatchTarget:
    observation: ObjectObservation = field(repr=False)
    # Supplied by a trusted observation port; checksum alone is not a generation identity.
    generation: str = field(repr=False)
    artifact_kind: str = "RAW_RESPONSE"


@dataclass(frozen=True)
class ReviewBatch:
    scope: SurveyScope = field(repr=False)
    targets: tuple[BatchTarget, ...] = field(repr=False)
    environment: str = "SYNTHETIC_LOCAL"

    def digest(self) -> str:
        return hashlib.sha256(_batch_bytes(self)).hexdigest()


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone required")
    return value.astimezone(UTC).isoformat()


def _batch_bytes(batch: ReviewBatch) -> bytes:
    scope = batch.scope
    if batch.environment != "SYNTHETIC_LOCAL" or scope.storage_backend != "LOCAL_PRIVATE":
        raise ValueError("Unsupported environment")
    if (
        scope.policy_version != "source-artifact-retention-v1"
        or not scope.database_id.strip()
        or not scope.namespace.strip()
    ):
        raise ValueError("Invalid scope")
    keys = [target.observation.object_key for target in batch.targets]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("Empty or duplicate targets")
    targets = []
    for target in sorted(batch.targets, key=lambda value: value.observation.object_key):
        obj = target.observation
        if (
            target.artifact_kind not in {"RAW_RESPONSE", "REJECTS"}
            or not obj.object_key.strip()
            or not target.generation.strip()
            or obj.created_at is None
            or obj.source_owned is not True
            or re.fullmatch(r"[0-9a-f]{64}", obj.checksum) is None
            or type(obj.byte_size) is not int
            or obj.byte_size < 0
        ):
            raise ValueError("Unproven object")
        targets.append(
            {
                "kind": target.artifact_kind,
                "key": obj.object_key,
                "checksum": obj.checksum,
                "byte_size": obj.byte_size,
                "created_at": _utc(obj.created_at),
                "generation": target.generation,
            }
        )
    payload = {
        "format": "source-cleanup-review-batch-v2",
        "policy": scope.policy_version,
        "database": scope.database_id,
        "namespace": scope.namespace,
        "backend": scope.storage_backend,
        "environment": batch.environment,
        "targets": targets,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class ApprovalEvidence:
    batch_digest: str
    policy_version: str
    receipt_id: str
    pm_actor: str
    db_security_actor: str
    executor: str
    valid_from: datetime
    expires_at: datetime


class ApprovalVerifier(Protocol):
    async def verify(self, *, batch_digest: str, executor: str) -> ApprovalEvidence | None:
        """Validate identity, authority, revocation and review evidence in a trusted store."""
        ...


@dataclass(frozen=True)
class LockedObservation:
    scope: SurveyScope = field(repr=False)
    targets: tuple[BatchTarget, ...] = field(repr=False)
    complete: bool


class GuardedInspection(Protocol):
    async def assert_held(self) -> None:
        """Raise if writer exclusion is unavailable or has been lost."""
        ...

    async def observe(self) -> LockedObservation:
        """Read actual bytes, identity and provenance under the same guard."""
        ...

    @property
    def references(self) -> ReferenceReader: ...


class InspectionGuard(Protocol):
    def acquire(self, batch: ReviewBatch) -> AbstractAsyncContextManager[GuardedInspection]:
        """Exclude all object writers/reusers/ref creators; no production implementation yet."""
        ...


@dataclass(frozen=True)
class PreflightResult:
    checks_passed: bool
    reason: str


class _FixedInventory(InventoryReader):
    def __init__(self, observed: LockedObservation) -> None:
        self._observed = observed

    def read_inventory(self) -> Inventory:
        return Inventory(
            self._observed.scope.namespace,
            tuple(t.observation for t in self._observed.targets),
            self._observed.complete,
        )


def _approval_matches(
    receipt: ApprovalEvidence | None, digest: str, batch: ReviewBatch, executor: str, now: datetime
) -> bool:
    if receipt is None:
        return False
    _utc(now)
    _utc(receipt.valid_from)
    _utc(receipt.expires_at)
    return (
        receipt.batch_digest == digest
        and receipt.policy_version == batch.scope.policy_version
        and bool(receipt.receipt_id.strip())
        and bool(receipt.pm_actor.strip())
        and bool(receipt.db_security_actor.strip())
        and receipt.executor == executor
        and receipt.valid_from <= now < receipt.expires_at
    )


async def check_batch(
    *,
    batch: ReviewBatch,
    executor: str,
    clock: Callable[[], datetime],
    approvals: ApprovalVerifier | None = None,
    guard: InspectionGuard | None = None,
) -> PreflightResult:
    """Dry-run only. Guard is released on return; caller must NOT delete using this result."""
    try:
        digest = batch.digest()
        _utc(clock())
        if not executor.strip():
            return PreflightResult(False, "EXECUTOR_MISSING")
    except (ValueError, TypeError, AttributeError):
        return PreflightResult(False, "BATCH_INVALID")
    if approvals is None or guard is None:
        return PreflightResult(False, "TRUSTED_PORT_UNAVAILABLE")
    try:
        receipt = await approvals.verify(batch_digest=digest, executor=executor)
        if not _approval_matches(receipt, digest, batch, executor, clock()):
            return PreflightResult(False, "APPROVAL_INVALID")
    except Exception:
        return PreflightResult(False, "APPROVAL_UNAVAILABLE")
    completed = False
    try:
        async with guard.acquire(batch) as locked:
            await locked.assert_held()
            observed = await locked.observe()
            actual = ReviewBatch(observed.scope, observed.targets, batch.environment)
            if not observed.complete or actual.digest() != digest:
                return PreflightResult(False, "OBJECT_OR_SCOPE_CHANGED")
            result = await survey_candidates(
                scope=batch.scope,
                now=clock(),
                inventory=_FixedInventory(observed),
                references=locked.references,
            )
            if not result.complete or any(
                item.decision is not SurveyDecision.REVIEW_CANDIDATE for item in result.items
            ):
                return PreflightResult(False, "FINAL_REFERENCE_CHECK_FAILED")
            # Revalidate after potentially slow reads/lock acquisition: expiry/revocation may change.
            receipt = await approvals.verify(batch_digest=digest, executor=executor)
            if not _approval_matches(receipt, digest, batch, executor, clock()):
                return PreflightResult(False, "APPROVAL_INVALID")
            await locked.assert_held()
            completed = True
        return PreflightResult(completed, "DRY_RUN_CHECKS_PASSED" if completed else "GUARDED_CHECK_UNAVAILABLE")
    except Exception:
        return PreflightResult(False, "GUARDED_CHECK_UNAVAILABLE")
