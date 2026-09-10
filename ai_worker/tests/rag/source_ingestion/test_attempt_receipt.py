from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotAttemptReceipt, SnapshotIngestionDecision


def receipt(status, failure_code, has_snapshot):
    return SnapshotAttemptReceipt(
        operation_id=uuid4(),
        snapshot_id=uuid4() if has_snapshot else None,
        run_group_key="synthetic-receipt",
        attempt_number=1,
        run_status=status,
        started_at=datetime(2026, 9, 11, tzinfo=UTC),
        finished_at=datetime(2026, 9, 11, tzinfo=UTC),
        duration_ms=0,
        failure_code=failure_code,
    )


@pytest.mark.parametrize(
    ("status", "failure", "snapshot", "decision"),
    [
        ("SUCCEEDED", None, True, SnapshotIngestionDecision.CREATED),
        ("SUCCEEDED_WITH_REJECTIONS", None, True, SnapshotIngestionDecision.CREATED),
        ("NO_CHANGE", None, True, SnapshotIngestionDecision.NO_CHANGE),
        ("FAILED", "SOURCE_VERSION_CONFLICT", False, SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT),
        ("FAILED", "SOURCE_VERSION_INVALID", False, SnapshotIngestionDecision.VALIDATION_FAILED),
        ("FAILED", "SOURCE_VERSION_BINDING_MISMATCH", False, SnapshotIngestionDecision.VALIDATION_FAILED),
        ("FAILED", "EMPTY_RESULT", False, SnapshotIngestionDecision.VALIDATION_FAILED),
        ("FAILED", "REJECTION_LIMIT_EXCEEDED", False, SnapshotIngestionDecision.VALIDATION_FAILED),
    ],
)
def test_receipt_serializes_explicit_decision(status, failure, snapshot, decision):
    result = receipt(status, failure, snapshot)
    assert result.decision is decision
    assert asdict(result)["decision"] is decision


@pytest.mark.parametrize(
    ("status", "failure", "snapshot"),
    [
        ("SUCCEEDED", None, False),
        ("SUCCEEDED_WITH_REJECTIONS", "EMPTY_RESULT", True),
        ("SUCCEEDED", "SOURCE_VERSION_CONFLICT", True),
        ("NO_CHANGE", "EMPTY_RESULT", True),
        ("NO_CHANGE", None, False),
        ("FAILED", "SOURCE_VERSION_INVALID", True),
        ("FAILED", None, False),
        ("FAILED", "NEW_FAILURE", False),
        ("RUNNING", None, False),
        ("FUTURE_STATUS", None, True),
        ("FAILED", "TIMEOUT", False),
    ],
)
def test_receipt_rejects_inconsistent_or_unmapped_states(status, failure, snapshot):
    with pytest.raises(ValueError):
        receipt(status, failure, snapshot)
