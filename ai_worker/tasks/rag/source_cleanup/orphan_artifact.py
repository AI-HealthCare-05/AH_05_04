"""실패한 Source 적재가 남긴 Artifact의 수동 cleanup 경계입니다."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

_CHECKSUM = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,99}\Z")
_OBJECT_KEY = re.compile(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})\.artifact\Z")
_RESULT_REASONS = {
    "INTENT": "DELETE_INTENT",
    "DELETED": "DELETE_CONFIRMED",
    "BLOCKED": "REFERENCE_PRESENT",
    "MANUAL_REVIEW": "CHECKSUM_CONFLICT",
    "NOT_FOUND": "ARTIFACT_NOT_FOUND",
    "UNKNOWN": "DELETE_RESULT_UNKNOWN",
}


class CleanupResult(StrEnum):
    INTENT = "INTENT"
    DELETED = "DELETED"
    BLOCKED = "BLOCKED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CleanupTarget:
    artifact_key: str
    object_key: str = field(repr=False)
    checksum: str

    def __post_init__(self) -> None:
        if not isinstance(self.checksum, str) or _CHECKSUM.fullmatch(self.checksum) is None:
            raise ValueError("CLEANUP_TARGET_INVALID")
        if (
            not self.artifact_key.strip()
            or len(self.artifact_key) > 500
            or any(ord(character) < 32 for character in self.artifact_key)
        ):
            raise ValueError("CLEANUP_TARGET_INVALID")
        match = _OBJECT_KEY.fullmatch(self.object_key)
        if match is None or match.group(1) != self.checksum[:2] or match.group(2) != self.checksum:
            raise ValueError("CLEANUP_TARGET_INVALID")


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    request_id: UUID
    run_group_key: str
    ingestion_run_id: UUID | None
    targets: tuple[CleanupTarget, ...]
    failure_reason: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, UUID) or (
            self.ingestion_run_id is not None and not isinstance(self.ingestion_run_id, UUID)
        ):
            raise ValueError("CLEANUP_REQUEST_INVALID")
        if (
            not self.run_group_key.strip()
            or len(self.run_group_key) > 100
            or any(ord(character) < 32 for character in self.run_group_key)
        ):
            raise ValueError("CLEANUP_REQUEST_INVALID")
        if not self.targets or len({target.object_key for target in self.targets}) != len(self.targets):
            raise ValueError("CLEANUP_REQUEST_INVALID")
        if (
            self.failure_reason != "MFDS_LABEL_TRANSACTION_FAILED"
            or not isinstance(self.requested_at, datetime)
            or not _aware(self.requested_at)
        ):
            raise ValueError("CLEANUP_REQUEST_INVALID")


@dataclass(frozen=True, slots=True)
class ArtifactReferences:
    artifact_receipts: int
    ingestion_runs: int
    snapshot_members: int
    snapshots: int
    checksum_conflicts: int

    def __post_init__(self) -> None:
        values = (
            self.artifact_receipts,
            self.ingestion_runs,
            self.snapshot_members,
            self.snapshots,
            self.checksum_conflicts,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("CLEANUP_REFERENCE_RESULT_INVALID")

    @property
    def referenced(self) -> bool:
        return any((self.artifact_receipts, self.ingestion_runs, self.snapshot_members, self.snapshots))


@dataclass(frozen=True, slots=True)
class CleanupItemReceipt:
    artifact_key: str
    object_key: str = field(repr=False)
    checksum: str
    references: ArtifactReferences
    result: CleanupResult
    reason: str

    def __post_init__(self) -> None:
        CleanupTarget(self.artifact_key, self.object_key, self.checksum)
        if not isinstance(self.result, CleanupResult):
            raise ValueError("CLEANUP_ITEM_RECEIPT_INVALID")
        if _RESULT_REASONS.get(self.result.value) != self.reason:
            raise ValueError("CLEANUP_ITEM_RECEIPT_INVALID")
        if self.result is CleanupResult.MANUAL_REVIEW and not self.references.checksum_conflicts:
            raise ValueError("CLEANUP_ITEM_RECEIPT_INVALID")
        if self.result is CleanupResult.BLOCKED and (
            not self.references.referenced or self.references.checksum_conflicts
        ):
            raise ValueError("CLEANUP_ITEM_RECEIPT_INVALID")
        if self.result in {
            CleanupResult.INTENT,
            CleanupResult.DELETED,
            CleanupResult.NOT_FOUND,
            CleanupResult.UNKNOWN,
        } and (self.references.referenced or self.references.checksum_conflicts):
            raise ValueError("CLEANUP_ITEM_RECEIPT_INVALID")


@dataclass(frozen=True, slots=True)
class CleanupReceipt:
    request_id: UUID
    executor: str
    executed_at: datetime
    items: tuple[CleanupItemReceipt, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.request_id, UUID)
            or not isinstance(self.executor, str)
            or _SAFE_ACTOR.fullmatch(self.executor) is None
            or not isinstance(self.executed_at, datetime)
            or not _aware(self.executed_at)
            or not self.items
        ):
            raise ValueError("CLEANUP_RECEIPT_INVALID")


class CleanupRequestJournal(Protocol):
    def append_request(self, request: CleanupRequest) -> None: ...


class CleanupReceiptJournal(Protocol):
    def append_receipt(self, receipt: CleanupReceipt) -> None: ...


class ArtifactReferenceInspector(Protocol):
    async def inspect(self, target: CleanupTarget) -> ArtifactReferences: ...


class ArtifactCleanupExecutor(Protocol):
    def exists(self, target: CleanupTarget) -> bool: ...

    def delete_verified(self, target: CleanupTarget) -> None: ...


async def execute_cleanup_request(
    *,
    request: CleanupRequest,
    executor: str,
    executed_at: datetime,
    references: ArtifactReferenceInspector,
    artifacts: ArtifactCleanupExecutor,
    receipts: CleanupReceiptJournal,
) -> CleanupReceipt:
    """전역 DB 참조와 bytes를 다시 확인한 뒤 한 요청을 수동 실행합니다."""
    items = []
    for target in request.targets:
        observed = await references.inspect(target)
        if observed.checksum_conflicts:
            item = _item(target, observed, CleanupResult.MANUAL_REVIEW, "CHECKSUM_CONFLICT")
        elif observed.referenced:
            item = _item(target, observed, CleanupResult.BLOCKED, "REFERENCE_PRESENT")
        elif not artifacts.exists(target):
            item = _item(target, observed, CleanupResult.NOT_FOUND, "ARTIFACT_NOT_FOUND")
        else:
            # 삭제 직전 fresh query. 첫 조회 결과를 권한으로 재사용하지 않습니다.
            final = await references.inspect(target)
            if final.checksum_conflicts:
                item = _item(target, final, CleanupResult.MANUAL_REVIEW, "CHECKSUM_CONFLICT")
            elif final.referenced:
                item = _item(target, final, CleanupResult.BLOCKED, "REFERENCE_PRESENT")
            else:
                # 이 기록이 실패하면 삭제하지 않습니다. 최종 receipt 실패 시에도 복구 단서가 남습니다.
                receipts.append_receipt(
                    CleanupReceipt(
                        request.request_id,
                        executor,
                        executed_at,
                        (_item(target, final, CleanupResult.INTENT, "DELETE_INTENT"),),
                    )
                )
                try:
                    artifacts.delete_verified(target)
                except Exception:
                    item = _item(target, final, CleanupResult.UNKNOWN, "DELETE_RESULT_UNKNOWN")
                else:
                    item = _item(target, final, CleanupResult.DELETED, "DELETE_CONFIRMED")
        items.append(item)
    receipt = CleanupReceipt(request.request_id, executor, executed_at, tuple(items))
    receipts.append_receipt(receipt)
    return receipt


def _item(
    target: CleanupTarget,
    references: ArtifactReferences,
    result: CleanupResult,
    reason: str,
) -> CleanupItemReceipt:
    return CleanupItemReceipt(
        target.artifact_key,
        target.object_key,
        target.checksum,
        references,
        result,
        reason,
    )


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
