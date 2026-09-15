"""#591 전용 적재 명령의 비밀정보·입력 경계를 검증합니다."""

import argparse
from pathlib import Path

import pytest

from ai_worker.admin.mfds_label_writer import MfdsLabelWriterConfig, parse_collected_at


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
