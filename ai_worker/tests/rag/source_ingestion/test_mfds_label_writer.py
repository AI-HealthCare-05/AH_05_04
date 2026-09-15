"""#591 전용 적재 명령의 비밀정보·입력 경계를 검증합니다."""

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.engine import URL

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.adapters.local_private_source_cleanup import LocalPrivateCleanupExecutorJournal
from ai_worker.admin import mfds_label_writer
from ai_worker.admin.mfds_label_writer import (
    MfdsLabelPostCommitVerificationError,
    MfdsLabelTransactionCleanupRequiredError,
    MfdsLabelWriterConfig,
    main,
    parse_collected_at,
    record_transaction_cleanup_request,
    run_ingestion,
)
from ai_worker.admin.source_writer import WriterConfig
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata, RawArtifactStore, StoredRawArtifact
from ai_worker.tasks.rag.source_ingestion.mfds_label import MfdsLabelPersistenceReceipt
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionDecision,
    SnapshotPersistenceResult,
)


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "SOURCE_WRITER_HOST": "localhost",
        "SOURCE_WRITER_PORT": "5432",
        "SOURCE_WRITER_NAME": "synthetic",
        "SOURCE_WRITER_USER": "synthetic_writer",
        "SOURCE_WRITER_PASSWORD": "synthetic-password",
        "SOURCE_WRITER_ACTOR": "synthetic-actor",
        "SOURCE_ARTIFACT_STORAGE_BACKEND": "LOCAL_PRIVATE",
        "SOURCE_ARTIFACT_READER_ROOT": str(tmp_path / "private-reader"),
        "SOURCE_ARTIFACT_FINALIZER_COMMAND": "/usr/local/bin/source591-preserve",
        "SOURCE_CLEANUP_JOURNAL_ROOT": str(tmp_path / "cleanup-journal"),
        "MFDS_LABEL_SOURCE_CODE": "SYNTHETIC_MFDS_LABEL",
        "MFDS_LABEL_ENDPOINT_CODE": "SYNTHETIC_LABEL_XML",
        "MFDS_LABEL_OPERATION_CODE": "GET_SELECTED_LABELS",
        "MFDS_LABEL_ENDPOINT_RECEIPT_HASH": "a" * 64,
    }


def test_writer_config_requires_isolated_local_private_inputs(tmp_path: Path) -> None:
    config = MfdsLabelWriterConfig.from_environment(_environment(tmp_path))
    rendered = repr(config)
    assert "synthetic-password" not in rendered
    assert str(tmp_path) not in rendered
    assert "a" * 64 not in rendered


@pytest.mark.parametrize(
    "changes",
    [
        {"SOURCE_ARTIFACT_STORAGE_BACKEND": "DISABLED"},
        {"SOURCE_ARTIFACT_READER_ROOT": "relative"},
        {"SOURCE_ARTIFACT_FINALIZER_COMMAND": "relative"},
        {"SOURCE_ARTIFACT_LOCAL_ROOT": "/private/writable-final-root"},
        {"SOURCE_CLEANUP_JOURNAL_ROOT": "relative"},
        {"MFDS_LABEL_SOURCE_CODE": ""},
        {"MFDS_LABEL_ENDPOINT_RECEIPT_HASH": "invalid"},
        {"DB_PASSWORD": "mixed-secret"},
    ],
)
def test_writer_config_fails_closed_on_incomplete_or_mixed_environment(tmp_path: Path, changes: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        MfdsLabelWriterConfig.from_environment({**_environment(tmp_path), **changes})


def test_collected_at_requires_timezone() -> None:
    assert parse_collected_at("2026-09-15T06:00:00Z").utcoffset() is not None
    with pytest.raises(argparse.ArgumentTypeError):
        parse_collected_at("2026-09-15T06:00:00")


def test_rollback_request_uses_only_artifacts_confirmed_by_store(tmp_path: Path) -> None:
    raw = b"synthetic-source"
    checksum = hashlib.sha256(raw).hexdigest()
    stored = StoredRawArtifact(
        page_number=1,
        metadata=RawArtifactMetadata("mfds-label/200610660/EE.xml", checksum, len(raw), "application/xml"),
        storage_backend="LOCAL_PRIVATE",
        object_key=f"sha256/{checksum[:2]}/{checksum}.artifact",
    )
    journal = mfds_label_writer.LocalPrivateCleanupRequestJournal(tmp_path / "journal")

    request_id = record_transaction_cleanup_request(
        journal=journal,
        run_group_key="mfds-label-200610660-synthetic",
        ingestion_run_id=UUID("00000000-0000-4000-8000-000000000610"),
        stored_artifacts=[stored, stored],
        requested_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )

    assert request_id is not None
    request = LocalPrivateCleanupExecutorJournal(tmp_path / "journal").read_request(request_id)
    assert request.ingestion_run_id == UUID("00000000-0000-4000-8000-000000000610")
    assert request.failure_reason == "MFDS_LABEL_TRANSACTION_FAILED"
    assert request.targets[0].object_key == stored.object_key
    assert len(request.targets) == 1


def test_command_reports_safe_cleanup_request_id_after_rollback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_id = UUID("00000000-0000-4000-8000-000000000613")

    async def fail_before_commit(*args: object, **kwargs: object) -> None:
        raise MfdsLabelTransactionCleanupRequiredError(request_id)

    monkeypatch.setattr(mfds_label_writer.MfdsLabelWriterConfig, "from_environment", lambda env: object())
    monkeypatch.setattr(mfds_label_writer, "run_ingestion", fail_before_commit)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mfds-label-writer", "200610660", "--input-dir", "/private/input", "--collected-at", "2026-09-15T06:00:00Z"],
    )

    assert main() == 1
    error = capsys.readouterr().err
    assert str(request_id) in error
    assert "cleanup_request_id" in error


@pytest.mark.asyncio
async def test_commit_failure_after_artifact_write_creates_private_cleanup_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = b"synthetic-source"
    checksum = hashlib.sha256(raw).hexdigest()
    source = tmp_path / "EE.xml"
    source.write_bytes(raw)
    metadata = RawArtifactMetadata("mfds-label/200610660/EE.xml", checksum, len(raw), "application/xml")
    plan = SimpleNamespace(source_version="synthetic-version", documents=(SimpleNamespace(metadata=metadata),))
    persistence = MfdsLabelPersistenceReceipt(
        SnapshotPersistenceResult(
            SnapshotIngestionDecision.CREATED,
            UUID("00000000-0000-4000-8000-000000000001"),
            UUID("00000000-0000-4000-8000-000000000002"),
            UUID("00000000-0000-4000-8000-000000000003"),
        ),
        "synthetic-version",
        "a" * 64,
        (),
    )

    async def fake_persist(*, artifact_store: RawArtifactStore, **kwargs: object) -> MfdsLabelPersistenceReceipt:
        artifact_store.put_verified(page_number=1, file_path=source, metadata=metadata)
        return persistence

    async def no_validation(session: object) -> None:
        return None

    class _Transaction:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            raise RuntimeError("synthetic commit failure")

    class _Sessions:
        def begin(self) -> _Transaction:
            return _Transaction()

    class _Engine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr(mfds_label_writer, "load_mfds_label_plan", lambda **kwargs: plan)
    monkeypatch.setattr(mfds_label_writer, "persist_mfds_label_plan", fake_persist)
    monkeypatch.setattr(mfds_label_writer, "validate_source_writer_session", no_validation)
    monkeypatch.setattr(mfds_label_writer, "lock_source_artifact_mutation", no_validation)
    monkeypatch.setattr(mfds_label_writer, "create_async_engine", lambda *args, **kwargs: _Engine())
    monkeypatch.setattr(mfds_label_writer, "async_sessionmaker", lambda *args, **kwargs: _Sessions())
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o500)

    class _SyntheticFinalizingStore:
        def __init__(self) -> None:
            self.finalized: list[StoredRawArtifact] = []

        def put_verified(self, **kwargs: object) -> StoredRawArtifact:
            artifact_root.chmod(0o700)
            try:
                stored = LocalPrivateSourceArtifactStore(artifact_root).put_verified(
                    **kwargs  # type: ignore[arg-type]
                )
            finally:
                artifact_root.chmod(0o500)
            self.finalized.append(stored)
            return stored

    monkeypatch.setattr(
        mfds_label_writer,
        "FinalizingLocalPrivateSourceArtifactStore",
        lambda **kwargs: _SyntheticFinalizingStore(),
    )
    config = MfdsLabelWriterConfig(
        writer=WriterConfig(URL.create("postgresql+asyncpg", password="synthetic"), "synthetic-writer"),
        artifact_reader_root=artifact_root,
        artifact_finalizer_command=tmp_path / "source591-preserve",
        cleanup_journal_root=tmp_path / "journal",
        identity=SourceOperationIdentity("SYNTHETIC_SOURCE", "SYNTHETIC_ENDPOINT", "SYNTHETIC_OPERATION"),
        endpoint_receipt_hash="a" * 64,
    )

    with pytest.raises(MfdsLabelTransactionCleanupRequiredError):
        await run_ingestion(
            config,
            item_seq="200610660",
            input_dir=tmp_path,
            collected_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
            include_e_drug=False,
        )

    request_file = next((tmp_path / "journal" / "requests").iterdir())
    request = LocalPrivateCleanupExecutorJournal(tmp_path / "journal").read_request(UUID(request_file.stem))
    assert request.ingestion_run_id == persistence.persistence.ingestion_run_id
    assert request.targets[0].checksum == checksum


def test_command_does_not_report_rollback_after_post_commit_requery_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    persistence = MfdsLabelPersistenceReceipt(
        persistence=SnapshotPersistenceResult(
            decision=SnapshotIngestionDecision.CREATED,
            operation_id=UUID("00000000-0000-4000-8000-000000000001"),
            ingestion_run_id=UUID("00000000-0000-4000-8000-000000000002"),
            snapshot_id=UUID("00000000-0000-4000-8000-000000000003"),
        ),
        source_version="api:2026-09-15T06:00:00Z:sha256:" + "a" * 64,
        canonical_checksum="a" * 64,
        member_ids=(),
    )

    async def fail_after_commit(*args: object, **kwargs: object) -> None:
        raise MfdsLabelPostCommitVerificationError(persistence)

    monkeypatch.setattr(mfds_label_writer.MfdsLabelWriterConfig, "from_environment", lambda env: object())
    monkeypatch.setattr(mfds_label_writer, "run_ingestion", fail_after_commit)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mfds-label-writer", "200610660", "--input-dir", "/private/input", "--collected-at", "2026-09-15T06:00:00Z"],
    )

    assert main() == 1
    error = capsys.readouterr().err
    assert "committed but post-commit verification failed" in error
    assert "Do not rerun" in error
    assert "rolled back" not in error
