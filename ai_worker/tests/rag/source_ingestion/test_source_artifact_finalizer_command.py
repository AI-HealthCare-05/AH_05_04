import hashlib
import io
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_worker.admin import source_artifact_finalizer
from ai_worker.admin.source_artifact_finalizer import (
    ArtifactFinalizerConfig,
    main,
    preserve_from_stdin,
)


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "SOURCE_ARTIFACT_LOCAL_ROOT": str(tmp_path / "final"),
        "SOURCE_ARTIFACT_FINALIZER_STAGING_ROOT": str(tmp_path / "staging"),
    }


def test_finalizer_preserves_stdin_and_removes_owned_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"<synthetic>true</synthetic>"
    checksum = hashlib.sha256(content).hexdigest()
    config = ArtifactFinalizerConfig.from_environment(_environment(tmp_path))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(content)))

    object_key = preserve_from_stdin(
        config,
        checksum=checksum,
        byte_size=len(content),
        content_type="application/xml",
    )

    assert (config.artifact_root / object_key).read_bytes() == content
    assert list(config.staging_root.iterdir()) == []
    assert stat.S_IMODE(config.staging_root.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "changes",
    [
        {"SOURCE_ARTIFACT_LOCAL_ROOT": "relative"},
        {"SOURCE_ARTIFACT_FINALIZER_STAGING_ROOT": "relative"},
        {"SOURCE_WRITER_PASSWORD": "mixed-secret"},
        {"SOURCE_CLEANUP_EXECUTOR_PASSWORD": "mixed-secret"},
    ],
)
def test_finalizer_config_rejects_invalid_or_mixed_environment(tmp_path: Path, changes: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ArtifactFinalizerConfig.from_environment({**_environment(tmp_path), **changes})


def test_finalizer_command_returns_only_safe_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    content = b"synthetic"
    checksum = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(source_artifact_finalizer.os, "environ", _environment(tmp_path))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(content)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source-artifact-finalizer",
            "--checksum",
            checksum,
            "--byte-size",
            str(len(content)),
            "--content-type",
            "application/xml",
        ],
    )

    assert main() == 0
    response = json.loads(capsys.readouterr().out)
    assert response == {
        "object_key": f"sha256/{checksum[:2]}/{checksum}.artifact",
        "schema": "source-artifact-finalize@1",
        "storage_backend": "LOCAL_PRIVATE",
    }
    assert str(tmp_path) not in json.dumps(response)


def test_finalizer_rejects_more_bytes_than_declared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = ArtifactFinalizerConfig.from_environment(_environment(tmp_path))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"too-long")))

    with pytest.raises(ValueError, match="exceeds"):
        preserve_from_stdin(
            config,
            checksum=hashlib.sha256(b"too").hexdigest(),
            byte_size=3,
            content_type="application/xml",
        )

    assert list(config.staging_root.iterdir()) == []
