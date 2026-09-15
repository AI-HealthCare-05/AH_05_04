import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from ai_worker.adapters.local_private_source_cleanup import (
    LocalPrivateArtifactCleanupExecutor,
    LocalPrivateCleanupExecutorJournal,
    LocalPrivateCleanupRequestJournal,
)
from ai_worker.tasks.rag.source_cleanup.orphan_artifact import (
    ArtifactReferences,
    CleanupItemReceipt,
    CleanupReceipt,
    CleanupRequest,
    CleanupResult,
    CleanupTarget,
)


def _request() -> CleanupRequest:
    target = CleanupTarget("mfds-label/200610660/EE.xml", "sha256/aa/" + "a" * 64 + ".artifact", "a" * 64)
    return CleanupRequest(
        UUID("00000000-0000-4000-8000-000000000613"),
        "mfds-label-200610660-safeattempt",
        None,
        (target,),
        "MFDS_LABEL_TRANSACTION_FAILED",
        datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )


def test_private_journal_round_trips_request_without_sensitive_fields(tmp_path: Path) -> None:
    writer = LocalPrivateCleanupRequestJournal(tmp_path / "journal")
    journal = LocalPrivateCleanupExecutorJournal(tmp_path / "journal")
    request = _request()
    writer.append_request(request)

    assert journal.read_request(request.request_id) == request
    raw = next((tmp_path / "journal" / "requests").iterdir()).read_text()
    assert set(json.loads(raw)) == {
        "schema_version",
        "request_id",
        "run_group_key",
        "ingestion_run_id",
        "targets",
        "failure_reason",
        "requested_at",
    }
    assert "credential" not in raw.lower()
    assert "database" not in raw.lower()
    assert "<xml" not in raw.lower()
    mode = os.stat(next((tmp_path / "journal" / "requests").iterdir())).st_mode
    assert mode & 0o337 == 0


def test_private_journal_is_append_only_for_same_request(tmp_path: Path) -> None:
    journal = LocalPrivateCleanupRequestJournal(tmp_path / "journal")
    request = _request()
    journal.append_request(request)
    with pytest.raises(FileExistsError):
        journal.append_request(request)


def test_executor_verifies_checksum_before_deleting(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    executor = LocalPrivateArtifactCleanupExecutor(root)
    target = _request().targets[0]
    path = root / target.object_key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="CHECKSUM_MISMATCH"):
        executor.delete_verified(target)
    assert path.exists()


def test_executor_does_not_treat_symlink_as_missing_or_delete_target(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    executor = LocalPrivateArtifactCleanupExecutor(root)
    target = _request().targets[0]
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    path = root / target.object_key
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    assert executor.exists(target)
    with pytest.raises(ValueError, match="ARTIFACT_INVALID"):
        executor.delete_verified(target)
    assert outside.read_bytes() == b"outside"


def test_receipt_records_reference_scope_and_result(tmp_path: Path) -> None:
    request = _request()
    item = CleanupItemReceipt(
        request.targets[0].artifact_key,
        request.targets[0].object_key,
        request.targets[0].checksum,
        ArtifactReferences(0, 0, 0, 0, 0),
        CleanupResult.DELETED,
        "DELETE_CONFIRMED",
    )
    journal = LocalPrivateCleanupExecutorJournal(tmp_path / "journal")
    journal.append_receipt(CleanupReceipt(request.request_id, "synthetic-executor", request.requested_at, (item,)))
    raw = json.loads(next((tmp_path / "journal" / "receipts").iterdir()).read_text())
    assert raw["items"][0]["references"] == {
        "artifact_receipts": 0,
        "ingestion_runs": 0,
        "snapshot_members": 0,
        "snapshots": 0,
        "checksum_conflicts": 0,
    }
    assert raw["items"][0]["result"] == "DELETED"
    restored = journal.read_receipts(request.request_id)
    assert len(restored) == 1
    assert restored[0].items == (item,)
