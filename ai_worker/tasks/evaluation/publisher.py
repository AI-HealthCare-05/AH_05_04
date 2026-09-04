from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import platform
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.common import CanonicalUuid

_BUNDLE_FILENAMES = frozenset(
    {
        "run.json",
        "cases.jsonl",
        "metrics.json",
        "suite-results.json",
        "failures.jsonl",
        "result-content-manifest.json",
        "report.md",
    }
)
_UUID_ADAPTER = TypeAdapter(CanonicalUuid)
_UNSUPPORTED_ERRNOS = {
    errno.ENOSYS,
    errno.EXDEV,
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.ENOSYS),
    getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
}
_PATH_ERRNOS = {errno.ELOOP, errno.ENOTDIR, errno.ENOENT, errno.EACCES, errno.EPERM}
type FileIdentity = tuple[int, int]


def _normalize_error(error: BaseException) -> BaseException:
    if isinstance(error, EvaluationValidationError):
        return error
    if isinstance(error, (AttributeError, NotImplementedError, TypeError)):
        return EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED)
    if isinstance(error, OSError):
        if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            return EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)
        if error.errno in _UNSUPPORTED_ERRNOS:
            return EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED)
        if error.errno in _PATH_ERRNOS:
            return EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
        return EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    if isinstance(error, Exception):
        return EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    return error


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED)
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_allowed_root(path: Path) -> tuple[Path, int]:
    absolute = Path(os.path.abspath(path))
    try:
        current_fd = os.open(absolute.anchor, _directory_flags())
        for index, component in enumerate(absolute.parts[1:]):
            try:
                next_fd = os.open(component, _directory_flags(), dir_fd=current_fd)
            except FileNotFoundError:
                if index != len(absolute.parts[1:]) - 1:
                    raise
                os.mkdir(component, 0o700, dir_fd=current_fd)
                next_fd = os.open(component, _directory_flags(), dir_fd=current_fd)
                os.fchmod(next_fd, 0o700)
            metadata = os.fstat(next_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_fd)
                raise OSError(errno.ENOTDIR, "directory required")
            os.close(current_fd)
            current_fd = next_fd
        return absolute, current_fd
    except BaseException as error:
        if "current_fd" in locals():
            try:
                os.close(current_fd)
            except OSError:
                pass
        raise _normalize_error(error) from None


def _identity(directory_fd: int, name: str) -> FileIdentity | None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return metadata.st_dev, metadata.st_ino


def _descriptor_identity(descriptor: int) -> FileIdentity:
    metadata = os.fstat(descriptor)
    return metadata.st_dev, metadata.st_ino


def _create_file(directory_fd: int, name: str, payload: bytes) -> FileIdentity:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    identity: FileIdentity | None = None
    close_attempted = False
    try:
        entry_identity = _identity(directory_fd, name)
        descriptor_identity = _descriptor_identity(descriptor)
        if entry_identity is None or entry_identity != descriptor_identity:
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
        identity = descriptor_identity
        os.fchmod(descriptor, 0o600)
        if os.write(descriptor, payload) != len(payload):
            raise OSError(errno.EIO, "short write")
        os.fsync(descriptor)
        close_attempted = True
        os.close(descriptor)
        return identity
    except BaseException as error:
        cleanup_error: BaseException | None = None
        if not close_attempted:
            try:
                os.close(descriptor)
            except BaseException as close_error:
                cleanup_error = close_error
        try:
            if identity is not None:
                _remove_file_if_owned(directory_fd, name, identity)
        except BaseException as remove_error:
            cleanup_error = cleanup_error or remove_error
        if cleanup_error is not None:
            raise cleanup_error from error
        raise


def _remove_file_if_owned(directory_fd: int, name: str, identity: FileIdentity) -> None:
    quarantine_name = _isolate_owned_entry(directory_fd, name, identity)
    if quarantine_name is None:
        return
    try:
        os.unlink(quarantine_name, dir_fd=directory_fd)
    except BaseException:
        _restore_isolated_entry(directory_fd, quarantine_name, name)
        raise


def exclusive_rename(parent_fd: int, source: str, target: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    system = platform.system()
    if system == "Darwin":
        function = libc.renameatx_np
        flag = 0x00000004
    elif system == "Linux":
        function = libc.renameat2
        flag = 1
    else:
        raise NotImplementedError
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = function(parent_fd, os.fsencode(source), parent_fd, os.fsencode(target), flag)
    if result == -1:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _restore_isolated_entry(directory_fd: int, quarantine_name: str, original_name: str) -> None:
    if _identity(directory_fd, quarantine_name) is None:
        return
    exclusive_rename(directory_fd, quarantine_name, original_name)


def _isolate_owned_entry(
    directory_fd: int,
    name: str,
    identity: FileIdentity,
) -> str | None:
    quarantine_name = f".cleanup.{uuid4()}"
    try:
        exclusive_rename(directory_fd, name, quarantine_name)
    except FileNotFoundError:
        return None
    try:
        isolated_identity = _identity(directory_fd, quarantine_name)
    except BaseException:
        _restore_isolated_entry(directory_fd, quarantine_name, name)
        raise
    if isolated_identity != identity:
        _restore_isolated_entry(directory_fd, quarantine_name, name)
        raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    return quarantine_name


def _remove_isolated_directory(
    directory_fd: int,
    quarantine_name: str,
    original_name: str,
) -> bool:
    try:
        os.rmdir(quarantine_name, dir_fd=directory_fd)
    except OSError as error:
        _restore_isolated_entry(directory_fd, quarantine_name, original_name)
        if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
            raise
        return False
    return True


def _cleanup_staging(
    root_fd: int,
    staging_fd: int | None,
    staging_name: str,
    staging_identity: FileIdentity | None,
    created_files: Mapping[str, FileIdentity],
) -> bool:
    if staging_fd is None or staging_identity is None:
        return False
    quarantine_name: str | None = None
    try:
        quarantine_name = _isolate_owned_entry(root_fd, staging_name, staging_identity)
        if quarantine_name is None:
            return False
        for name, identity in created_files.items():
            _remove_file_if_owned(staging_fd, name, identity)
    except BaseException:
        if quarantine_name is not None:
            _restore_isolated_entry(root_fd, quarantine_name, staging_name)
        raise
    finally:
        os.close(staging_fd)
    return _remove_isolated_directory(root_fd, quarantine_name, staging_name)


def _cleanup_unopened_staging(
    root_fd: int,
    staging_name: str,
    staging_identity: FileIdentity | None,
    *,
    created: bool,
) -> bool:
    if not created or staging_identity is None:
        return False
    quarantine_name = _isolate_owned_entry(root_fd, staging_name, staging_identity)
    if quarantine_name is None:
        return False
    return _remove_isolated_directory(root_fd, quarantine_name, staging_name)


def _verify_staging_entry(
    root_fd: int,
    staging_fd: int,
    staging_name: str,
    staging_identity: FileIdentity,
    *,
    verify_contents: bool = False,
    created_files: Mapping[str, FileIdentity] | None = None,
    expected_files: Mapping[str, bytes] | None = None,
) -> None:
    descriptor_identity = _descriptor_identity(staging_fd)
    if descriptor_identity != staging_identity or _identity(root_fd, staging_name) != descriptor_identity:
        raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    if verify_contents and set(os.listdir(staging_fd)) != _BUNDLE_FILENAMES:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    if verify_contents:
        if (
            created_files is None
            or expected_files is None
            or set(created_files) != _BUNDLE_FILENAMES
            or set(expected_files) != _BUNDLE_FILENAMES
        ):
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
        for name, identity in created_files.items():
            _verify_file_content(staging_fd, name, identity, expected_files[name])


def _verify_file_content(
    directory_fd: int,
    name: str,
    identity: FileIdentity,
    expected: bytes,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        if _descriptor_identity(descriptor) != identity or _identity(directory_fd, name) != identity:
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        if size != len(expected) or digest.digest() != hashlib.sha256(expected).digest():
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
        if _descriptor_identity(descriptor) != identity or _identity(directory_fd, name) != identity:
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    finally:
        os.close(descriptor)


def _cleanup_published(
    root_fd: int,
    published_fd: int,
    run_id: str,
    published_identity: FileIdentity | None,
    created_files: Mapping[str, FileIdentity],
) -> bool:
    if published_identity is None:
        os.close(published_fd)
        return False
    quarantine_name: str | None = None
    try:
        quarantine_name = _isolate_owned_entry(root_fd, run_id, published_identity)
        if quarantine_name is None:
            return False
        for name, identity in created_files.items():
            _remove_file_if_owned(published_fd, name, identity)
    except BaseException:
        if quarantine_name is not None:
            _restore_isolated_entry(root_fd, quarantine_name, run_id)
        raise
    finally:
        os.close(published_fd)
    return _remove_isolated_directory(root_fd, quarantine_name, run_id)


@dataclass(slots=True)
class _RunPublication:
    root_fd: int
    run_id: str
    lock_name: str = field(init=False)
    staging_name: str = field(init=False)
    lock_identity: FileIdentity | None = None
    staging_created: bool = False
    staging_bound: bool = False
    staging_identity: FileIdentity | None = None
    staging_fd: int | None = None
    created_files: dict[str, FileIdentity] = field(default_factory=dict)
    renamed: bool = False
    committed: bool = False

    def __post_init__(self) -> None:
        self.lock_name = f"{self.run_id}.lock"
        self.staging_name = f".{self.run_id}.tmp.{uuid4()}"

    def execute(self, files: Mapping[str, bytes]) -> None:
        if _identity(self.root_fd, self.run_id) is not None or _identity(self.root_fd, self.lock_name) is not None:
            raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)
        self.lock_identity = _create_file(self.root_fd, self.lock_name, b"")
        if _identity(self.root_fd, self.run_id) is not None:
            raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)
        os.mkdir(self.staging_name, 0o700, dir_fd=self.root_fd)
        self.staging_created = True
        self.staging_identity = _identity(self.root_fd, self.staging_name)
        if self.staging_identity is None:
            raise OSError(errno.ENOENT, "staging directory missing")
        self.staging_fd = os.open(self.staging_name, _directory_flags(), dir_fd=self.root_fd)
        _verify_staging_entry(
            self.root_fd,
            self.staging_fd,
            self.staging_name,
            self.staging_identity,
        )
        self.staging_bound = True
        os.fchmod(self.staging_fd, 0o700)
        for name in sorted(files, key=lambda value: value.encode("utf-16-be")):
            self.created_files[name] = _create_file(self.staging_fd, name, files[name])
        os.fsync(self.staging_fd)
        _verify_staging_entry(
            self.root_fd,
            self.staging_fd,
            self.staging_name,
            self.staging_identity,
            verify_contents=True,
            created_files=self.created_files,
            expected_files=files,
        )
        # Re-verify immediately adjacent to the rename call, with no work in
        # between, to shrink (but not eliminate) the TOCTOU window: rename
        # operates on the name, not this fd, so a swap landing in that gap is
        # only detectable, never preventable — the post-rename check below is
        # what actually fails closed on it.
        _verify_staging_entry(self.root_fd, self.staging_fd, self.staging_name, self.staging_identity)
        exclusive_rename(self.root_fd, self.staging_name, self.run_id)
        self.renamed = True
        _verify_staging_entry(
            self.root_fd,
            self.staging_fd,
            self.run_id,
            self.staging_identity,
            verify_contents=True,
            created_files=self.created_files,
            expected_files=files,
        )
        os.fsync(self.root_fd)
        self.remove_lock()
        os.fsync(self.root_fd)
        self.committed = True

    def remove_lock(self) -> None:
        if self.lock_identity is not None:
            _remove_file_if_owned(self.root_fd, self.lock_name, self.lock_identity)
            self.lock_identity = None

    def cleanup(self) -> BaseException | None:
        cleaned_directory_entry = False
        cleanup_errors: list[BaseException] = []
        try:
            if self.staging_fd is not None and self.committed:
                os.close(self.staging_fd)
            elif self.staging_fd is not None and not self.staging_bound:
                os.close(self.staging_fd)
            elif self.staging_fd is not None and self.renamed:
                cleaned_directory_entry = _cleanup_published(
                    self.root_fd,
                    self.staging_fd,
                    self.run_id,
                    self.staging_identity,
                    self.created_files,
                )
            elif self.staging_fd is not None:
                cleaned_directory_entry = _cleanup_staging(
                    self.root_fd,
                    self.staging_fd,
                    self.staging_name,
                    self.staging_identity,
                    self.created_files,
                )
            elif self.staging_created:
                cleaned_directory_entry = _cleanup_unopened_staging(
                    self.root_fd,
                    self.staging_name,
                    self.staging_identity,
                    created=self.staging_created,
                )
        except BaseException as error:
            cleanup_errors.append(error)
        self.staging_fd = None
        had_lock = self.lock_identity is not None
        try:
            self.remove_lock()
        except BaseException as error:
            cleanup_errors.append(error)
        if cleaned_directory_entry or had_lock:
            try:
                os.fsync(self.root_fd)
            except BaseException as error:
                cleanup_errors.append(error)
        return _normalize_error(cleanup_errors[0]) if cleanup_errors else None


def _validate_publication_input(run_id: str, files: Mapping[str, bytes]) -> None:
    if set(files) != _BUNDLE_FILENAMES:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    if unicodedata.normalize("NFC", run_id) != run_id:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    try:
        _UUID_ADAPTER.validate_python(run_id)
    except ValidationError:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from None


def publish_run_directory(*, allowed_root: Path, run_id: str, files: Mapping[str, bytes]) -> Path:
    _validate_publication_input(run_id, files)
    root_path, root_fd = _open_allowed_root(allowed_root)
    publication = _RunPublication(root_fd=root_fd, run_id=run_id)
    primary_error: BaseException | None = None
    try:
        publication.execute(files)
    except BaseException as error:
        primary_error = _normalize_error(error)

    cleanup_error = publication.cleanup()
    try:
        os.close(root_fd)
    except OSError as error:
        cleanup_error = cleanup_error or _normalize_error(error)

    if cleanup_error is not None:
        raise cleanup_error from None
    if primary_error is not None:
        raise primary_error from None
    return root_path / run_id
