from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation import publisher as publisher_module
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.publisher import publish_run_directory

RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


def _bundle() -> dict[str, bytes]:
    return {
        "run.json": b"run",
        "cases.jsonl": b"cases\n",
        "metrics.json": b"metrics",
        "suite-results.json": b"suite",
        "failures.jsonl": b"",
        "result-content-manifest.json": b"manifest",
        "report.md": b"report\n",
    }


def test_publish_run_directory_is_private_and_complete(tmp_path: Path) -> None:
    destination = publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert destination == tmp_path / RUN_ID
    assert destination.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in destination.iterdir())
    assert sorted(path.name for path in tmp_path.iterdir()) == [RUN_ID]


def test_publish_does_not_overwrite_existing_directory(tmp_path: Path) -> None:
    destination = tmp_path / RUN_ID
    destination.mkdir()
    marker = destination / "operator-owned"
    marker.write_bytes(b"preserve")

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.RESULT_PATH_CONFLICT
    assert marker.read_bytes() == b"preserve"


def test_publish_does_not_remove_operator_owned_lock(tmp_path: Path) -> None:
    lock = tmp_path / f"{RUN_ID}.lock"
    lock.write_bytes(b"operator-owned")

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.RESULT_PATH_CONFLICT
    assert lock.read_bytes() == b"operator-owned"


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", "not-a-uuid", "e\u0301"])
def test_publish_rejects_noncanonical_run_id(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=run_id, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID
    assert list(tmp_path.iterdir()) == []


def test_publish_rejects_symlinked_allowed_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=linked_root, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID
    assert list(real_root.iterdir()) == []


def test_publish_cleans_staging_and_lock_after_short_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_write = os.write

    def short_write(descriptor: int, payload: bytes) -> int:
        if payload:
            return max(0, len(payload) - 1)
        return real_write(descriptor, payload)

    monkeypatch.setattr(publisher_module.os, "write", short_write)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []


def test_publish_cleans_staging_and_lock_after_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publisher_module.os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError(errno.EIO)))

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []


def test_publish_cleans_staging_and_lock_after_staging_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    root_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)

    def fail_staging_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        if stat.S_ISDIR(metadata.st_mode) and identity != root_identity:
            raise OSError(errno.EIO, "staging fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(publisher_module.os, "fsync", fail_staging_fsync)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []


def test_publish_cleans_created_staging_when_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = os.open

    def fail_staging_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if isinstance(path, str) and path.startswith(f".{RUN_ID}.tmp."):
            raise OSError(errno.EIO, "staging open failed")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(publisher_module.os, "open", fail_staging_open)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("root_fsync_number", [1, 2])
def test_publish_rolls_back_final_directory_after_parent_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_fsync_number: int,
) -> None:
    real_fsync = os.fsync
    root_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    root_fsync_calls = 0

    def fail_selected_root_fsync(descriptor: int) -> None:
        nonlocal root_fsync_calls
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == root_identity:
            root_fsync_calls += 1
            if root_fsync_calls == root_fsync_number:
                raise OSError(errno.EIO, "parent fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(publisher_module.os, "fsync", fail_selected_root_fsync)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []
    assert root_fsync_calls == root_fsync_number + 1


def test_publish_rolls_back_final_directory_after_lock_removal_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_remove = publisher_module._remove_file_if_owned
    real_fsync = os.fsync
    failed_once = False
    root_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    root_fsync_calls = 0

    def count_root_fsync(descriptor: int) -> None:
        nonlocal root_fsync_calls
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == root_identity:
            root_fsync_calls += 1
        real_fsync(descriptor)

    def fail_first_lock_removal(directory_fd: int, name: str, identity: tuple[int, int]) -> None:
        nonlocal failed_once
        if name.endswith(".lock") and not failed_once:
            failed_once = True
            raise OSError(errno.EIO, "lock removal failed")
        real_remove(directory_fd, name, identity)

    monkeypatch.setattr(publisher_module, "_remove_file_if_owned", fail_first_lock_removal)
    monkeypatch.setattr(publisher_module.os, "fsync", count_root_fsync)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []
    assert root_fsync_calls == 2


def test_publish_fails_closed_when_exclusive_rename_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_rename(_parent_fd: int, _source: str, _target: str) -> None:
        raise OSError(errno.EXDEV, "cross filesystem")

    monkeypatch.setattr(publisher_module, "exclusive_rename", fail_rename)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED
    assert not (tmp_path / RUN_ID).exists()
    assert list(tmp_path.iterdir()) == []


def test_publish_rejects_incomplete_file_set_before_creating_state(tmp_path: Path) -> None:
    files = _bundle()
    files.pop("report.md")

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=files)

    assert caught.value.code is EvaluationErrorCode.MANIFEST_INVALID
    assert list(tmp_path.iterdir()) == []
