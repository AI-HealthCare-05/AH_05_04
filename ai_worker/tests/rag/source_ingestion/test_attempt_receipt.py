from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_worker.tasks.rag.source_client.contracts import SourceFailureCode
from ai_worker.tasks.rag.source_ingestion.failure_codes import (
    COLLECTION_EMPTY_RESULT,
    SNAPSHOT_POLICY_EMPTY_RESULT,
    IngestionProcessingFailureCode,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotAttemptReceipt, SnapshotIngestionDecision
from ai_worker.tasks.rag.source_ingestion.snapshot_policy import SnapshotPolicyFailureCode
from ai_worker.tasks.rag.source_ingestion.source_version import SourceVersionFailureCode


def receipt(status, failure_code, has_snapshot, reason=None):
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
        validation_reason_code=reason,
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
        ("FAILED", "EMPTY_RESULT", False),
    ],
)
def test_receipt_rejects_inconsistent_or_unmapped_states(status, failure, snapshot):
    with pytest.raises(ValueError):
        receipt(status, failure, snapshot)


@pytest.mark.parametrize("code", list(SourceFailureCode))
def test_every_collection_failure_has_collection_decision(code):
    reason = COLLECTION_EMPTY_RESULT if code is SourceFailureCode.EMPTY_RESULT else None
    assert receipt("FAILED", code.value, False, reason).decision is SnapshotIngestionDecision.COLLECTION_FAILED


@pytest.mark.parametrize(
    "code", [*SourceVersionFailureCode, *IngestionProcessingFailureCode, *SnapshotPolicyFailureCode]
)
def test_every_validation_failure_has_validation_decision(code):
    reason = SNAPSHOT_POLICY_EMPTY_RESULT if code.value == "EMPTY_RESULT" else None
    assert receipt("FAILED", code.value, False, reason).decision is SnapshotIngestionDecision.VALIDATION_FAILED


@pytest.mark.parametrize("reason", [None, "FUTURE_REASON", "EMPTY_RESULT"])
def test_ambiguous_empty_result_is_not_guessed(reason):
    with pytest.raises(ValueError, match="decision is unavailable"):
        receipt("FAILED", "EMPTY_RESULT", False, reason)


@pytest.mark.parametrize("reason", [COLLECTION_EMPTY_RESULT, SNAPSHOT_POLICY_EMPTY_RESULT])
@pytest.mark.parametrize("code", ["TIMEOUT", "PARSER_VALIDATION_FAILED", "SOURCE_VERSION_CONFLICT", "FUTURE_FAILURE"])
def test_empty_discriminator_cannot_label_other_failure(reason, code):
    with pytest.raises(ValueError, match="decision is unavailable"):
        receipt("FAILED", code, False, reason)


def test_failure_vocabulary_changes_require_contract_review():
    assert {code.value for code in SourceFailureCode} == {
        "INVALID_REQUEST",
        "REQUEST_REJECTED",
        "AUTHENTICATION_FAILED",
        "RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
        "TIMEOUT",
        "RESPONSE_TOO_LARGE",
        "PAGE_LIMIT_EXCEEDED",
        "CONTENT_TYPE_MISMATCH",
        "SCHEMA_DRIFT",
        "EMPTY_RESULT",
        "REDIRECT_REJECTED",
        "DESTINATION_REJECTED",
        "XML_SECURITY_VIOLATION",
        "RETRY_BUDGET_EXHAUSTED",
    }
    assert {code.value for code in SourceVersionFailureCode} == {
        "SOURCE_VERSION_INVALID",
        "SOURCE_VERSION_BINDING_MISMATCH",
    }
    assert {code.value for code in IngestionProcessingFailureCode} == {
        "PARSER_VALIDATION_FAILED",
        "REJECTION_LIMIT_EXCEEDED",
    }
    assert {code.value for code in SnapshotPolicyFailureCode} == {"EMPTY_RESULT", "REJECTION_LIMIT_EXCEEDED"}
