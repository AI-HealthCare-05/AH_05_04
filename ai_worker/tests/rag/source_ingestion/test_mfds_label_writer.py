"""#591 전용 적재 명령의 비밀정보·입력 경계를 검증합니다."""

import argparse
import sys
from pathlib import Path
from uuid import UUID

import pytest

from ai_worker.admin import mfds_label_writer
from ai_worker.admin.mfds_label_writer import (
    MfdsLabelPostCommitVerificationError,
    MfdsLabelWriterConfig,
    main,
    parse_collected_at,
)
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
        "SOURCE_ARTIFACT_LOCAL_ROOT": str(tmp_path / "private"),
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
        {"SOURCE_ARTIFACT_LOCAL_ROOT": "relative"},
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
