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


def _candidate_bundle() -> dict[str, bytes]:
    return {**_bundle(), "comparison.json": b"comparison"}


def test_publish_run_directory_is_private_and_complete(tmp_path: Path) -> None:
    destination = publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert destination == tmp_path / RUN_ID
    assert destination.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in destination.iterdir())
    assert sorted(path.name for path in tmp_path.iterdir()) == [RUN_ID]


def test_publisher_atomically_publishes_candidate_bundle_with_comparison(tmp_path: Path) -> None:
    files = _candidate_bundle()

    published = publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=files)

    assert {path.name for path in published.iterdir()} == set(files)
    assert (published / "comparison.json").read_bytes() == files["comparison.json"]


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


def test_publish_preserves_unbound_staging_when_initial_identity_stat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_stat = os.stat
    failed_once = False

    def fail_initial_staging_stat(*args: object, **kwargs: object) -> os.stat_result:
        nonlocal failed_once
        path = args[0] if args else kwargs.get("path")
        if isinstance(path, str) and path.startswith(f".{RUN_ID}.tmp.") and not failed_once:
            failed_once = True
            raise OSError(errno.EIO, "staging identity stat failed")
        return real_stat(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(publisher_module.os, "stat", fail_initial_staging_stat)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert failed_once
    assert not (tmp_path / RUN_ID).exists()
    assert not (tmp_path / f"{RUN_ID}.lock").exists()
    staging_directories = list(tmp_path.glob(f".{RUN_ID}.tmp.*"))
    assert len(staging_directories) == 1
    assert list(staging_directories[0].iterdir()) == []


def test_publish_preserves_replaced_staging_when_initial_identity_stat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_stat = os.stat
    real_mkdir = os.mkdir
    staging_name: str | None = None
    swapped = False

    def swap_then_fail_staging_stat(*args: object, **kwargs: object) -> os.stat_result:
        nonlocal staging_name, swapped
        path = args[0] if args else kwargs.get("path")
        dir_fd = kwargs.get("dir_fd")
        if isinstance(path, str) and path.startswith(f".{RUN_ID}.tmp.") and not swapped:
            assert isinstance(dir_fd, int)
            staging_name = path
            swapped = True
            os.rename(path, f"{path}.original", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            real_mkdir(path, 0o700, dir_fd=dir_fd)
            raise OSError(errno.EIO, "staging identity stat failed")
        return real_stat(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(publisher_module.os, "stat", swap_then_fail_staging_stat)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert swapped
    assert staging_name is not None
    assert (tmp_path / staging_name).is_dir()
    assert (tmp_path / f"{staging_name}.original").is_dir()
    assert not (tmp_path / f"{RUN_ID}.lock").exists()


def test_publish_preserves_staging_when_descriptor_identity_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = os.open
    real_descriptor_identity = publisher_module._descriptor_identity
    real_fstat = os.fstat
    staging_descriptor: int | None = None

    def capture_staging_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal staging_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(f".{RUN_ID}.tmp."):
            staging_descriptor = descriptor
        return descriptor

    def fail_staging_descriptor_identity(descriptor: int) -> tuple[int, int]:
        if descriptor == staging_descriptor:
            raise OSError(errno.EIO, "staging descriptor identity failed")
        return real_descriptor_identity(descriptor)

    monkeypatch.setattr(publisher_module.os, "open", capture_staging_open)
    monkeypatch.setattr(publisher_module, "_descriptor_identity", fail_staging_descriptor_identity)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert staging_descriptor is not None
    with pytest.raises(OSError) as closed:
        real_fstat(staging_descriptor)
    assert closed.value.errno == errno.EBADF
    staging_directories = list(tmp_path.glob(f".{RUN_ID}.tmp.*"))
    assert len(staging_directories) == 1
    assert list(staging_directories[0].iterdir()) == []
    assert not (tmp_path / f"{RUN_ID}.lock").exists()


def test_publish_preserves_unbound_lock_when_initial_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = os.open
    real_fstat = os.fstat
    lock_descriptor: int | None = None

    def capture_lock_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal lock_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == f"{RUN_ID}.lock":
            lock_descriptor = descriptor
        return descriptor

    def fail_lock_fstat(descriptor: int) -> os.stat_result:
        if descriptor == lock_descriptor:
            raise OSError(errno.EIO, "lock identity fstat failed")
        return real_fstat(descriptor)

    monkeypatch.setattr(publisher_module.os, "open", capture_lock_open)
    monkeypatch.setattr(publisher_module.os, "fstat", fail_lock_fstat)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert lock_descriptor is not None
    with pytest.raises(OSError) as closed:
        real_fstat(lock_descriptor)
    assert closed.value.errno == errno.EBADF
    assert (tmp_path / f"{RUN_ID}.lock").read_bytes() == b""


def test_publish_preserves_replaced_lock_when_descriptor_identity_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = os.open
    real_descriptor_identity = publisher_module._descriptor_identity
    real_fstat = os.fstat
    lock_descriptor: int | None = None
    lock_root_fd: int | None = None
    swapped = False

    def capture_lock_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal lock_descriptor, lock_root_fd
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == f"{RUN_ID}.lock":
            lock_descriptor = descriptor
            lock_root_fd = dir_fd
        return descriptor

    def swap_then_fail_descriptor_identity(descriptor: int) -> tuple[int, int]:
        nonlocal swapped
        if descriptor != lock_descriptor:
            return real_descriptor_identity(descriptor)
        assert lock_root_fd is not None
        swapped = True
        os.rename(
            f"{RUN_ID}.lock",
            f"{RUN_ID}.lock.original",
            src_dir_fd=lock_root_fd,
            dst_dir_fd=lock_root_fd,
        )
        replacement_fd = real_open(
            f"{RUN_ID}.lock",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=lock_root_fd,
        )
        os.write(replacement_fd, b"replacement")
        os.close(replacement_fd)
        raise OSError(errno.EIO, "lock descriptor identity failed")

    monkeypatch.setattr(publisher_module.os, "open", capture_lock_open)
    monkeypatch.setattr(publisher_module, "_descriptor_identity", swap_then_fail_descriptor_identity)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert swapped
    assert lock_descriptor is not None
    with pytest.raises(OSError) as closed:
        real_fstat(lock_descriptor)
    assert closed.value.errno == errno.EBADF
    assert (tmp_path / f"{RUN_ID}.lock").read_bytes() == b"replacement"
    assert (tmp_path / f"{RUN_ID}.lock.original").read_bytes() == b""


def test_publish_preserves_lock_replacement_swapped_at_cleanup_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_exclusive_rename = publisher_module.exclusive_rename
    swapped = False

    def swap_lock_then_isolate(parent_fd: int, source: str, target: str) -> None:
        nonlocal swapped
        if source == f"{RUN_ID}.lock" and ".cleanup." in target and not swapped:
            swapped = True
            os.rename(source, f"{source}.original", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            replacement_fd = os.open(
                source,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
            os.write(replacement_fd, b"replacement")
            os.close(replacement_fd)
        real_exclusive_rename(parent_fd, source, target)

    monkeypatch.setattr(publisher_module, "exclusive_rename", swap_lock_then_isolate)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert swapped
    assert not (tmp_path / RUN_ID).exists()
    assert (tmp_path / f"{RUN_ID}.lock").read_bytes() == b"replacement"
    assert (tmp_path / f"{RUN_ID}.lock.original").read_bytes() == b""


def test_publish_fails_closed_when_staging_entry_is_replaced_before_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    real_mkdir = os.mkdir
    root_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    staging_name: str | None = None
    staging_root_fd: int | None = None
    staging_descriptor: int | None = None
    swapped = False

    def capture_staging_mkdir(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal staging_name, staging_root_fd
        real_mkdir(path, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(f".{RUN_ID}.tmp.") and not swapped:
            staging_name = path
            staging_root_fd = dir_fd

    def replace_staging_after_fsync(descriptor: int) -> None:
        nonlocal staging_descriptor, swapped
        metadata = os.fstat(descriptor)
        real_fsync(descriptor)
        if swapped or not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) == root_identity:
            return
        assert staging_name is not None
        assert staging_root_fd is not None
        staging_descriptor = descriptor
        swapped = True
        os.rename(
            staging_name,
            f"{staging_name}.orphan",
            src_dir_fd=staging_root_fd,
            dst_dir_fd=staging_root_fd,
        )
        replacement = tmp_path / staging_name
        replacement.mkdir(mode=0o700)
        (replacement / "rogue.txt").write_bytes(b"rogue")

    monkeypatch.setattr(publisher_module.os, "mkdir", capture_staging_mkdir)
    monkeypatch.setattr(publisher_module.os, "fsync", replace_staging_after_fsync)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert staging_name is not None
    assert staging_descriptor is not None
    assert not (tmp_path / RUN_ID).exists()
    assert (tmp_path / staging_name / "rogue.txt").read_bytes() == b"rogue"
    assert not (tmp_path / f"{RUN_ID}.lock").exists()
    with pytest.raises(OSError) as closed:
        os.fstat(staging_descriptor)
    assert closed.value.errno == errno.EBADF


def test_publish_fails_closed_when_staging_entry_is_swapped_at_rename_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pre-rename check can close this gap: `exclusive_rename` renames by
    path, so a swap landing between the last check and the syscall itself is
    only detectable after the fact. This asserts the post-rename identity
    check catches it instead of silently publishing the swapped-in entry."""
    real_exclusive_rename = publisher_module.exclusive_rename
    swapped = False

    def swap_then_rename(parent_fd: int, source: str, target: str) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            os.rename(source, f"{source}.orphan", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.mkdir(source, 0o700, dir_fd=parent_fd)
            rogue_dir_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent_fd)
            try:
                rogue_fd = os.open(
                    "rogue.txt",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=rogue_dir_fd,
                )
                os.write(rogue_fd, b"rogue")
                os.close(rogue_fd)
            finally:
                os.close(rogue_dir_fd)
        real_exclusive_rename(parent_fd, source, target)

    monkeypatch.setattr(publisher_module, "exclusive_rename", swap_then_rename)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert swapped
    orphaned = list(tmp_path.glob(f".{RUN_ID}.tmp.*.orphan"))
    assert len(orphaned) == 1
    assert {path.name for path in orphaned[0].iterdir()} == set(_bundle())
    assert (tmp_path / RUN_ID / "rogue.txt").read_bytes() == b"rogue"
    assert not (tmp_path / f"{RUN_ID}.lock").exists()


def test_publish_fails_closed_when_staging_contains_an_unowned_extra_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    root_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    injected = False

    def inject_extra_file_after_staging_fsync(descriptor: int) -> None:
        nonlocal injected
        metadata = os.fstat(descriptor)
        real_fsync(descriptor)
        if injected or not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) == root_identity:
            return
        injected = True
        rogue_fd = os.open(
            "rogue.txt",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=descriptor,
        )
        os.write(rogue_fd, b"rogue")
        os.close(rogue_fd)

    monkeypatch.setattr(publisher_module.os, "fsync", inject_extra_file_after_staging_fsync)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.MANIFEST_INVALID
    assert injected
    assert not (tmp_path / RUN_ID).exists()
    assert not (tmp_path / f"{RUN_ID}.lock").exists()
    staging_directories = list(tmp_path.glob(f".{RUN_ID}.tmp.*"))
    assert len(staging_directories) == 1
    assert (staging_directories[0] / "rogue.txt").read_bytes() == b"rogue"


def test_publish_rejects_same_name_file_replacement_and_still_removes_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    root_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    replaced = False

    def replace_run_file_after_staging_fsync(descriptor: int) -> None:
        nonlocal replaced
        metadata = os.fstat(descriptor)
        real_fsync(descriptor)
        if replaced or not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) == root_identity:
            return
        replaced = True
        os.rename("run.json", tmp_path / "run.json.original", src_dir_fd=descriptor)
        replacement_fd = os.open(
            "run.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=descriptor,
        )
        os.write(replacement_fd, b"rogue")
        os.close(replacement_fd)

    monkeypatch.setattr(publisher_module.os, "fsync", replace_run_file_after_staging_fsync)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert replaced
    assert not (tmp_path / RUN_ID).exists()
    assert not (tmp_path / f"{RUN_ID}.lock").exists()
    staging_directories = list(tmp_path.glob(f".{RUN_ID}.tmp.*"))
    assert len(staging_directories) == 1
    assert (staging_directories[0] / "run.json").read_bytes() == b"rogue"
    assert (tmp_path / "run.json.original").read_bytes() == b"run"


def test_publish_rejects_extra_file_added_inside_rename_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_exclusive_rename = publisher_module.exclusive_rename
    injected = False

    def inject_extra_then_rename(parent_fd: int, source: str, target: str) -> None:
        nonlocal injected
        if target == RUN_ID and not injected:
            injected = True
            staging_fd = os.open(source, publisher_module._directory_flags(), dir_fd=parent_fd)
            try:
                rogue_fd = os.open(
                    "rogue.txt",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=staging_fd,
                )
                os.write(rogue_fd, b"rogue")
                os.close(rogue_fd)
            finally:
                os.close(staging_fd)
        real_exclusive_rename(parent_fd, source, target)

    monkeypatch.setattr(publisher_module, "exclusive_rename", inject_extra_then_rename)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.MANIFEST_INVALID
    assert (tmp_path / RUN_ID / "rogue.txt").read_bytes() == b"rogue"
    assert not (tmp_path / f"{RUN_ID}.lock").exists()


def test_publish_rejects_same_inode_content_rewrite_inside_rename_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_exclusive_rename = publisher_module.exclusive_rename
    rewritten = False

    def rewrite_then_rename(parent_fd: int, source: str, target: str) -> None:
        nonlocal rewritten
        if target == RUN_ID and not rewritten:
            rewritten = True
            staging_fd = os.open(source, publisher_module._directory_flags(), dir_fd=parent_fd)
            try:
                run_fd = os.open("run.json", os.O_WRONLY | os.O_TRUNC, dir_fd=staging_fd)
                try:
                    os.write(run_fd, b"rogue")
                    os.fsync(run_fd)
                finally:
                    os.close(run_fd)
            finally:
                os.close(staging_fd)
        real_exclusive_rename(parent_fd, source, target)

    monkeypatch.setattr(publisher_module, "exclusive_rename", rewrite_then_rename)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert rewritten
    assert not (tmp_path / RUN_ID).exists()
    assert not (tmp_path / f"{RUN_ID}.lock").exists()


def test_publish_preserves_staging_replacement_swapped_at_cleanup_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    real_exclusive_rename = publisher_module.exclusive_rename
    root_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    failed_staging_fsync = False
    swapped = False
    staging_name: str | None = None

    def fail_staging_fsync(descriptor: int) -> None:
        nonlocal failed_staging_fsync
        metadata = os.fstat(descriptor)
        if (
            not failed_staging_fsync
            and stat.S_ISDIR(metadata.st_mode)
            and (
                metadata.st_dev,
                metadata.st_ino,
            )
            != root_identity
        ):
            failed_staging_fsync = True
            raise OSError(errno.EIO, "staging fsync failed")
        real_fsync(descriptor)

    def swap_staging_then_isolate(parent_fd: int, source: str, target: str) -> None:
        nonlocal staging_name, swapped
        if source.startswith(f".{RUN_ID}.tmp.") and ".cleanup." in target and not swapped:
            staging_name = source
            swapped = True
            os.rename(source, f"{source}.original", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.mkdir(source, 0o700, dir_fd=parent_fd)
        real_exclusive_rename(parent_fd, source, target)

    monkeypatch.setattr(publisher_module.os, "fsync", fail_staging_fsync)
    monkeypatch.setattr(publisher_module, "exclusive_rename", swap_staging_then_isolate)

    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=_bundle())

    assert caught.value.code is EvaluationErrorCode.INTERNAL_ERROR
    assert failed_staging_fsync
    assert swapped
    assert staging_name is not None
    assert (tmp_path / staging_name).is_dir()
    assert list((tmp_path / staging_name).iterdir()) == []
    original = tmp_path / f"{staging_name}.original"
    assert original.is_dir()
    assert {path.name for path in original.iterdir()} == set(_bundle())
    assert not (tmp_path / f"{RUN_ID}.lock").exists()


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
    real_exclusive_rename = publisher_module.exclusive_rename

    def fail_publish_rename(parent_fd: int, source: str, target: str) -> None:
        if target == RUN_ID:
            raise OSError(errno.EXDEV, "cross filesystem")
        real_exclusive_rename(parent_fd, source, target)

    monkeypatch.setattr(publisher_module, "exclusive_rename", fail_publish_rename)

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


@pytest.mark.parametrize(
    "files",
    [
        {name: payload for name, payload in _candidate_bundle().items() if name != "report.md"},
        {**_bundle(), "unknown.json": b"unknown"},
    ],
    ids=["candidate-missing-core-file", "unknown-file"],
)
def test_publish_rejects_malformed_bundle_sets_before_creating_state(
    tmp_path: Path,
    files: dict[str, bytes],
) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        publish_run_directory(allowed_root=tmp_path, run_id=RUN_ID, files=files)

    assert caught.value.code is EvaluationErrorCode.MANIFEST_INVALID
    assert list(tmp_path.iterdir()) == []
