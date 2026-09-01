from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation import cli as cli_module
from ai_worker.tasks.evaluation.cli import main, publish_receipt_no_clobber
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FOUNDATION_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/dev-foundation-v1.dataset.json"


def test_cli_validates_fixture_without_creating_release_artifacts(tmp_path: Path) -> None:
    result = tmp_path / "receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 0
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert (receipt["execution_status"], receipt["decision_status"]) == ("COMPLETED", "N/A")
    assert receipt["release_eligible"] is False
    assert receipt["manifest_path"] == "retrieval/manifests/dev-foundation-v1.dataset.json"
    assert receipt["error_codes"] == []
    assert (
        not {
            "run_id",
            "metrics",
            "gate",
            "passed",
            "provider",
            "report",
        }
        & receipt.keys()
    )


def test_cli_does_not_overwrite_existing_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = tmp_path / "receipt.json"
    result.write_bytes(b"existing")

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    assert result.read_bytes() == b"existing"
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESULT_PATH_CONFLICT.value}\n"


def test_cli_rejects_existing_lock_without_removing_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = tmp_path / "receipt.json"
    lock = tmp_path / "receipt.json.lock"
    lock.write_bytes(b"operator-owned-stale-lock")

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    assert not result.exists()
    assert lock.read_bytes() == b"operator-owned-stale-lock"
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESULT_PATH_CONFLICT.value}\n"


def test_cli_rejects_symlink_parent_outside_allowed_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    (allowed_root / "linked").symlink_to(outside, target_is_directory=True)
    result = allowed_root / "linked/receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=allowed_root,
    )

    assert exit_code == 2
    assert not (outside / "receipt.json").exists()
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESOURCE_PATH_INVALID.value}\n"


def test_publish_receipt_uses_private_mode_and_cleans_temporary_files(tmp_path: Path) -> None:
    destination = tmp_path / "receipt.json"

    publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert destination.read_bytes() == b'{"safe":true}'
    assert destination.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == ["receipt.json"]


def test_publish_receipt_fails_closed_when_hard_link_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "receipt.json"

    def unsupported_link(
        source: os.PathLike[str] | str,
        target: os.PathLike[str] | str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del source, target, src_dir_fd, dst_dir_fd, follow_symlinks
        raise NotImplementedError

    monkeypatch.setattr(cli_module.os, "link", unsupported_link)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert caught.value.code is EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_publish_receipt_cleans_lock_and_temp_after_short_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "receipt.json"
    real_write = os.write
    write_count = 0

    def short_second_write(descriptor: int, payload: bytes) -> int:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            return 0
        return real_write(descriptor, payload)

    monkeypatch.setattr(cli_module.os, "write", short_second_write)

    with pytest.raises(OSError):
        publish_receipt_no_clobber(destination, b'{"safe":true}')

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_cli_rejects_result_outside_injected_allowed_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    result = tmp_path / "outside.json"

    exit_code = main(
        ["validate", "--manifest", str(FOUNDATION_MANIFEST), "--result", str(result)],
        allowed_result_root=allowed_root,
    )

    assert exit_code == 2
    assert not result.exists()
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESOURCE_PATH_INVALID.value}\n"


def test_cli_writes_invalid_receipt_for_loader_validation_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_manifest = REPOSITORY_ROOT / "evals/retrieval/manifests/missing.dataset.json"
    result = tmp_path / "receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(missing_manifest), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert (receipt["execution_status"], receipt["decision_status"]) == ("INVALID", None)
    assert receipt["error_codes"] == [EvaluationErrorCode.RESOURCE_MISSING.value]
    assert receipt["invalid_resource_paths"] == []
    assert capsys.readouterr().err == f"{EvaluationErrorCode.RESOURCE_MISSING.value}\n"


def test_cli_does_not_echo_sensitive_manifest_filename(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive_manifest = REPOSITORY_ROOT / "evals/retrieval/manifests/patient@example.com.dataset.json"
    result = tmp_path / "receipt.json"

    exit_code = main(
        ["validate", "--manifest", str(sensitive_manifest), "--result", str(result)],
        allowed_result_root=tmp_path,
    )

    assert exit_code == 2
    receipt_bytes = result.read_bytes()
    assert b"patient@example.com" not in receipt_bytes
    receipt = json.loads(receipt_bytes)
    assert receipt["manifest_path"] == "unresolved.dataset.json"
    assert receipt["error_codes"] == [EvaluationErrorCode.PRIVACY_VALUE_DETECTED.value]
    assert "patient@example.com" not in capsys.readouterr().err
