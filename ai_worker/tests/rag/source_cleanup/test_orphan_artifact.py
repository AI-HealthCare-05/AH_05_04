from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from ai_worker.tasks.rag.source_cleanup.orphan_artifact import (
    ArtifactReferences,
    CleanupItemReceipt,
    CleanupReceipt,
    CleanupRequest,
    CleanupResult,
    CleanupTarget,
    execute_cleanup_request,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
TARGET = CleanupTarget("mfds-label/200610660/EE.xml", "sha256/aa/" + "a" * 64 + ".artifact", "a" * 64)
REQUEST = CleanupRequest(
    UUID("00000000-0000-4000-8000-000000000613"),
    "mfds-label-200610660-safeattempt",
    None,
    (TARGET,),
    "MFDS_LABEL_TRANSACTION_FAILED",
    NOW,
)


class _References:
    def __init__(self, *observations: ArtifactReferences) -> None:
        self.observations = list(observations)
        self.calls = 0

    async def inspect(self, target: CleanupTarget) -> ArtifactReferences:
        self.calls += 1
        return self.observations.pop(0)


class _Artifacts:
    def __init__(self, *, exists: bool = True, fail: bool = False) -> None:
        self.present = exists
        self.fail = fail
        self.deleted = False

    def exists(self, target: CleanupTarget) -> bool:
        return self.present

    def delete_verified(self, target: CleanupTarget) -> None:
        if self.fail:
            raise OSError("synthetic unlink uncertainty")
        self.deleted = True
        self.present = False


class _Receipts:
    def __init__(self) -> None:
        self.values: list[CleanupReceipt] = []

    def append_receipt(self, receipt: CleanupReceipt) -> None:
        self.values.append(receipt)


ZERO = ArtifactReferences(0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("result", "reason", "references"),
    [
        (CleanupResult.INTENT, "DELETE_INTENT", ZERO),
        (CleanupResult.DELETED, "DELETE_CONFIRMED", ZERO),
        (CleanupResult.BLOCKED, "REFERENCE_PRESENT", ArtifactReferences(1, 1, 0, 0, 0)),
        (CleanupResult.MANUAL_REVIEW, "CHECKSUM_CONFLICT", ArtifactReferences(0, 0, 0, 0, 1)),
        (CleanupResult.NOT_FOUND, "ARTIFACT_NOT_FOUND", ZERO),
        (CleanupResult.UNKNOWN, "DELETE_RESULT_UNKNOWN", ZERO),
    ],
)
def test_receipt_accepts_each_explicit_result_boundary(
    result: CleanupResult, reason: str, references: ArtifactReferences
) -> None:
    assert CleanupItemReceipt(TARGET.artifact_key, TARGET.object_key, TARGET.checksum, references, result, reason)


def test_receipt_rejects_unsupported_result_and_mismatched_reason() -> None:
    with pytest.raises(ValueError, match="CLEANUP_ITEM_RECEIPT_INVALID"):
        CleanupItemReceipt(
            TARGET.artifact_key,
            TARGET.object_key,
            TARGET.checksum,
            ZERO,
            cast(CleanupResult, "UNSUPPORTED"),
            "DELETE_CONFIRMED",
        )
    with pytest.raises(ValueError, match="CLEANUP_ITEM_RECEIPT_INVALID"):
        CleanupItemReceipt(
            TARGET.artifact_key,
            TARGET.object_key,
            TARGET.checksum,
            ZERO,
            CleanupResult.DELETED,
            "REFERENCE_PRESENT",
        )


@pytest.mark.asyncio
async def test_cleanup_rechecks_references_then_deletes_and_records_receipt() -> None:
    references = _References(ZERO, ZERO)
    artifacts = _Artifacts()
    receipts = _Receipts()

    receipt = await execute_cleanup_request(
        request=REQUEST,
        executor="synthetic-cleanup-executor",
        executed_at=NOW,
        references=references,
        artifacts=artifacts,
        receipts=receipts,
    )

    assert references.calls == 2
    assert artifacts.deleted
    assert receipt.items[0].result is CleanupResult.DELETED
    assert receipt.items[0].references == ZERO
    assert len(receipts.values) == 2
    assert receipts.values[0].items[0].result is CleanupResult.INTENT
    assert receipts.values[1] == receipt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("references", "expected"),
    [
        (ArtifactReferences(1, 1, 0, 0, 0), CleanupResult.BLOCKED),
        (ArtifactReferences(0, 0, 0, 0, 1), CleanupResult.MANUAL_REVIEW),
    ],
)
async def test_cleanup_never_deletes_referenced_or_conflicting_artifact(
    references: ArtifactReferences, expected: CleanupResult
) -> None:
    artifacts = _Artifacts()
    receipt = await execute_cleanup_request(
        request=REQUEST,
        executor="synthetic-cleanup-executor",
        executed_at=NOW,
        references=_References(references),
        artifacts=artifacts,
        receipts=_Receipts(),
    )
    assert receipt.items[0].result is expected
    assert not artifacts.deleted


@pytest.mark.asyncio
async def test_cleanup_blocks_when_reference_appears_during_final_recheck() -> None:
    references = _References(ZERO, ArtifactReferences(1, 1, 1, 1, 0))
    artifacts = _Artifacts()
    receipt = await execute_cleanup_request(
        request=REQUEST,
        executor="synthetic-cleanup-executor",
        executed_at=NOW,
        references=references,
        artifacts=artifacts,
        receipts=_Receipts(),
    )
    assert receipt.items[0].result is CleanupResult.BLOCKED
    assert not artifacts.deleted


@pytest.mark.asyncio
async def test_cleanup_records_unknown_instead_of_claiming_failed_unlink_succeeded() -> None:
    receipt = await execute_cleanup_request(
        request=REQUEST,
        executor="synthetic-cleanup-executor",
        executed_at=NOW,
        references=_References(ZERO, ZERO),
        artifacts=_Artifacts(fail=True),
        receipts=_Receipts(),
    )
    assert receipt.items[0].result is CleanupResult.UNKNOWN
