from __future__ import annotations

import argparse
import errno
import os
import stat
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, normalize_resource_path
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.artifacts import ValidationReceipt
from ai_worker.tasks.evaluation.schemas.common import ImmutableReference

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_PRODUCTION_RESULT_ROOT = _REPOSITORY_ROOT / "evals/validation-results"
_VALIDATOR_VERSION = "1.0.0"
_UNKNOWN_DATASET_CODE = "unknown-dataset"
_UNKNOWN_DATASET_VERSION = "0.0.0"


_UNSUPPORTED_ERRNOS = {
    errno.ENOSYS,
    errno.EXDEV,
    errno.EPERM,
    getattr(errno, "ENOTSUP", errno.ENOSYS),
    getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
}
_PATH_ERRNOS = {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}


class _CliArgumentError(ValueError):
    pass


class _MissingDirectoryComponentError(EvaluationValidationError):
    def __init__(self) -> None:
        super().__init__(EvaluationErrorCode.RESOURCE_PATH_INVALID)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _CliArgumentError


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="python -m ai_worker.tasks.evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--result", required=True)
    return parser


def _normalized_publication_error(error: BaseException) -> BaseException:
    if isinstance(error, EvaluationValidationError):
        return error
    if isinstance(error, (AttributeError, NotImplementedError, TypeError)):
        return EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED)
    if isinstance(error, OSError):
        if error.errno == errno.EEXIST:
            return EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)
        if error.errno in _PATH_ERRNOS:
            return EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
        if error.errno in _UNSUPPORTED_ERRNOS:
            return EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED)
        return EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    if isinstance(error, Exception):
        return EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    return error


def _directory_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED)
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_directory_component(parent_fd: int, component: str) -> int:
    try:
        descriptor = os.open(component, _directory_flags(), dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError(errno.ENOTDIR, "directory component required")
        return descriptor
    except BaseException as error:
        normalized = (
            _MissingDirectoryComponentError()
            if isinstance(error, FileNotFoundError)
            else _normalized_publication_error(error)
        )
        if "descriptor" in locals():
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise normalized from None


def _open_absolute_directory(path: Path, *, create_final: bool = False) -> int:
    absolute = path.absolute()
    if not absolute.is_absolute() or any(part in {".", ".."} for part in absolute.parts):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    try:
        current_fd = os.open(absolute.anchor, _directory_flags())
    except BaseException as error:
        raise _normalized_publication_error(error) from None
    components = absolute.parts[1:]
    try:
        for index, component in enumerate(components):
            try:
                next_fd = _open_directory_component(current_fd, component)
            except EvaluationValidationError as error:
                is_final_missing = (
                    create_final and index == len(components) - 1 and isinstance(error, _MissingDirectoryComponentError)
                )
                if not is_final_missing:
                    raise
                try:
                    os.mkdir(component, 0o755, dir_fd=current_fd)
                    next_fd = _open_directory_component(current_fd, component)
                except BaseException as creation_error:
                    raise _normalized_publication_error(creation_error) from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def _prepare_allowed_root(root: Path) -> Path:
    root = root.absolute()
    try:
        descriptor = _open_absolute_directory(root, create_final=True)
        os.close(descriptor)
    except BaseException as error:
        raise _normalized_publication_error(error) from None
    return root


@dataclass(frozen=True, slots=True)
class _ValidatedDestination:
    allowed_root: Path
    relative_path: Path

    @property
    def path(self) -> Path:
        return self.allowed_root / self.relative_path


def _validate_result_path(raw_path: str, *, allowed_root: Path, production: bool) -> _ValidatedDestination:
    if "\x00" in raw_path or "\\" in raw_path or unicodedata.normalize("NFC", raw_path) != raw_path:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    supplied = Path(raw_path)
    if any(part in {".", ".."} for part in supplied.parts):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    if production:
        if supplied.is_absolute():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
        try:
            normalized = normalize_resource_path(raw_path)
        except EvaluationValidationError as error:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
        prefix = "evals/validation-results/"
        if not normalized.startswith(prefix):
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
        destination = _REPOSITORY_ROOT / normalized
    else:
        destination = supplied if supplied.is_absolute() else allowed_root / supplied
    destination = destination.absolute()
    try:
        relative = destination.relative_to(allowed_root)
    except ValueError as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    return _ValidatedDestination(allowed_root=allowed_root, relative_path=relative)


def _manifest_location(raw_path: str) -> tuple[Path, Path, str]:
    if "\x00" in raw_path or "\\" in raw_path or unicodedata.normalize("NFC", raw_path) != raw_path:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    supplied = Path(raw_path)
    if any(part in {".", ".."} for part in supplied.parts):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    manifest_path = (supplied if supplied.is_absolute() else Path.cwd() / supplied).absolute()
    evals_root = next((parent for parent in manifest_path.parents if parent.name == "evals"), None)
    if evals_root is None:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    try:
        manifest_relative = normalize_resource_path(manifest_path.relative_to(evals_root).as_posix())
    except (ValueError, EvaluationValidationError) as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
    try:
        validate_privacy_boundary({"manifest_path": manifest_relative})
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN:
            raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_DETECTED) from None
        raise
    return manifest_path, evals_root, manifest_relative


def _immutable_profile_ref(dataset: ValidatedDataset) -> ImmutableReference:
    profile = dataset.profile
    return ImmutableReference(
        id=profile.evaluation_profile_id,
        version=profile.evaluation_profile_version,
        hash=profile.evaluation_profile_hash,
    )


def _immutable_comparison_ref(dataset: ValidatedDataset) -> ImmutableReference:
    policy = dataset.comparison_policy
    return ImmutableReference(
        id=policy.comparison_policy_id,
        version=policy.comparison_policy_version,
        hash=policy.comparison_policy_hash,
    )


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _safe_invalid_paths(error: EvaluationValidationError) -> tuple[str, ...]:
    if error.safe_path is None:
        return ()
    candidate = error.safe_path.lstrip("/")
    if not candidate:
        return ()
    try:
        return (normalize_resource_path(candidate),)
    except EvaluationValidationError:
        return ()


def _receipt_bytes(
    *,
    manifest_path: str,
    execution_status: str,
    decision_status: str | None,
    error_codes: tuple[str, ...],
    invalid_resource_paths: tuple[str, ...],
    dataset: ValidatedDataset | None = None,
) -> bytes:
    manifest = None if dataset is None else dataset.manifest
    receipt = ValidationReceipt.model_validate(
        {
            "schema_id": "rag-eval.validation-receipt",
            "schema_version": "1.0.0",
            "validation_id": str(uuid4()),
            "validated_at": _utc_timestamp(),
            "validator_version": _VALIDATOR_VERSION,
            "manifest_path": manifest_path,
            "dataset_code": _UNKNOWN_DATASET_CODE if manifest is None else manifest.dataset_code,
            "dataset_version": _UNKNOWN_DATASET_VERSION if manifest is None else manifest.dataset_version,
            "dataset_manifest_sha256": None if manifest is None else manifest.manifest_sha256,
            "evaluation_profile_ref": None if dataset is None else _immutable_profile_ref(dataset),
            "comparison_policy_ref": None if dataset is None else _immutable_comparison_ref(dataset),
            "execution_status": execution_status,
            "decision_status": decision_status,
            "release_eligible": False,
            "error_codes": sorted(set(error_codes), key=lambda value: value.encode("utf-16-be")),
            "invalid_resource_paths": sorted(
                set(invalid_resource_paths),
                key=lambda value: value.encode("utf-16-be"),
            ),
        }
    )
    return canonical_json_bytes(receipt.model_dump(mode="json"))


FileIdentity = tuple[int, int]


def _entry_identity(directory_fd: int, name: str) -> FileIdentity | None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except BaseException as error:
        raise _normalized_publication_error(error) from None
    return (metadata.st_dev, metadata.st_ino)


def _entry_exists(directory_fd: int, name: str) -> bool:
    return _entry_identity(directory_fd, name) is not None


def _open_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _fsync_directory(directory_fd: int) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as error:
        unsupported = {errno.EINVAL, *_UNSUPPORTED_ERRNOS}
        if error.errno not in unsupported:
            raise _normalized_publication_error(error) from None


def _atomic_link(directory_fd: int, temporary_name: str, destination_name: str) -> None:
    try:
        os.link(
            temporary_name,
            destination_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except BaseException as error:
        raise _normalized_publication_error(error) from None


def _write_private_descriptor(descriptor: int, payload: bytes) -> None:
    try:
        if os.write(descriptor, payload) != len(payload):
            raise OSError(errno.EIO, "short private file write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(slots=True)
class _PublishFiles:
    directory_fd: int
    destination_name: str
    lock_name: str
    temporary_name: str
    lock_created: bool = False
    temporary_created: bool = False
    lock_identity: FileIdentity | None = None
    temporary_identity: FileIdentity | None = None

    def _create(self, name: str) -> tuple[int, FileIdentity]:
        try:
            descriptor = os.open(name, _open_flags(), 0o600, dir_fd=self.directory_fd)
        except BaseException as error:
            raise _normalized_publication_error(error) from None
        try:
            metadata = os.fstat(descriptor)
        except BaseException as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise _normalized_publication_error(error) from None
        return descriptor, (metadata.st_dev, metadata.st_ino)

    def acquire_lock(self) -> None:
        lock_payload = f"pid={os.getpid()}\ncreated_at={_utc_timestamp()}\n".encode("ascii")
        descriptor, identity = self._create(self.lock_name)
        self.lock_created = True
        self.lock_identity = identity
        _write_private_descriptor(descriptor, lock_payload)

    def write_temporary(self, payload: bytes) -> None:
        descriptor, identity = self._create(self.temporary_name)
        self.temporary_created = True
        self.temporary_identity = identity
        _write_private_descriptor(descriptor, payload)

    def _remove_if_owned(self, name: str, identity: FileIdentity | None) -> None:
        if identity is None:
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
        current_identity = _entry_identity(self.directory_fd, name)
        if current_identity is None:
            return
        if current_identity != identity:
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
        try:
            os.unlink(name, dir_fd=self.directory_fd)
        except BaseException as error:
            raise _normalized_publication_error(error) from None

    def remove_temporary(self) -> None:
        self._remove_if_owned(self.temporary_name, self.temporary_identity)
        self.temporary_created = False
        self.temporary_identity = None

    def cleanup(self) -> BaseException | None:
        first_error: BaseException | None = None
        names = (
            (self.temporary_name, self.temporary_created, self.temporary_identity),
            (self.lock_name, self.lock_created, self.lock_identity),
        )
        for name, created, identity in names:
            if not created:
                continue
            try:
                self._remove_if_owned(name, identity)
            except BaseException as error:
                first_error = first_error or error
        try:
            os.close(self.directory_fd)
        except OSError as error:
            first_error = first_error or _normalized_publication_error(error)
        return first_error


def _publish_in_directory(files: _PublishFiles, payload: bytes) -> None:
    if _entry_exists(files.directory_fd, files.destination_name):
        raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)
    files.acquire_lock()
    if _entry_exists(files.directory_fd, files.destination_name):
        raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)
    files.write_temporary(payload)
    if _entry_exists(files.directory_fd, files.destination_name):
        raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)
    _atomic_link(files.directory_fd, files.temporary_name, files.destination_name)
    files.remove_temporary()
    _fsync_directory(files.directory_fd)


def _open_destination_parent(destination: _ValidatedDestination) -> tuple[int, str]:
    parts = destination.relative_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    current_fd = _open_absolute_directory(destination.allowed_root)
    try:
        for component in parts[:-1]:
            next_fd = _open_directory_component(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except BaseException:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def _publish_validated_destination(destination: _ValidatedDestination, payload: bytes) -> None:
    try:
        directory_fd, destination_name = _open_destination_parent(destination)
    except BaseException as error:
        raise _normalized_publication_error(error) from None

    files = _PublishFiles(
        directory_fd=directory_fd,
        destination_name=destination_name,
        lock_name=f"{destination_name}.lock",
        temporary_name=f"{destination_name}.tmp.{uuid4()}",
    )
    primary_error: BaseException | None = None
    try:
        _publish_in_directory(files, payload)
    except BaseException as error:
        primary_error = _normalized_publication_error(error)
    cleanup_error = files.cleanup()
    if cleanup_error is not None:
        raise _normalized_publication_error(cleanup_error)
    if primary_error is not None:
        raise primary_error


def publish_receipt_no_clobber(destination: Path, payload: bytes) -> None:
    """Publish bytes through a private same-directory hard link without overwriting."""

    absolute = destination.absolute()
    validated = _ValidatedDestination(
        allowed_root=absolute.parent,
        relative_path=Path(absolute.name),
    )
    _publish_validated_destination(validated, payload)


def _emit_error(code: str) -> None:
    sys.stderr.write(f"{code}\n")


def _failure_exit_code(error: EvaluationValidationError) -> int:
    internal_codes = {
        EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED,
        EvaluationErrorCode.INTERNAL_ERROR,
    }
    return 1 if error.code in internal_codes else 2


def _emit_failure(error: EvaluationValidationError) -> int:
    _emit_error(error.code.value)
    return _failure_exit_code(error)


def _publish_outcome(
    destination: _ValidatedDestination,
    payload: bytes,
    *,
    intended_exit: int,
    code: str,
) -> int:
    try:
        _publish_validated_destination(destination, payload)
    except EvaluationValidationError as error:
        return _emit_failure(error)
    except Exception:
        _emit_error(EvaluationErrorCode.INTERNAL_ERROR.value)
        return 1
    _emit_error(code)
    return intended_exit


def main(argv: Sequence[str] | None = None, *, allowed_result_root: Path | None = None) -> int:
    """Validate one dataset and write only a non-release validation receipt."""

    try:
        arguments = _parser().parse_args(argv)
    except _CliArgumentError:
        _emit_error(EvaluationErrorCode.SCHEMA_INVALID.value)
        return 2
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 1

    production = allowed_result_root is None
    try:
        result_root = _prepare_allowed_root(
            _PRODUCTION_RESULT_ROOT if allowed_result_root is None else allowed_result_root
        )
        destination = _validate_result_path(
            arguments.result,
            allowed_root=result_root,
            production=production,
        )
    except EvaluationValidationError as error:
        return _emit_failure(error)

    manifest_relative = "unresolved.dataset.json"
    try:
        manifest_path, evals_root, manifest_relative = _manifest_location(arguments.manifest)
        dataset = load_dataset(manifest_path, evals_root=evals_root)
    except EvaluationValidationError as error:
        payload = _receipt_bytes(
            manifest_path=manifest_relative,
            execution_status="INVALID",
            decision_status=None,
            error_codes=(error.code.value,),
            invalid_resource_paths=_safe_invalid_paths(error),
        )
        return _publish_outcome(
            destination,
            payload,
            intended_exit=2,
            code=error.code.value,
        )
    except Exception:
        payload = _receipt_bytes(
            manifest_path=manifest_relative,
            execution_status="ERROR",
            decision_status=None,
            error_codes=(EvaluationErrorCode.INTERNAL_ERROR.value,),
            invalid_resource_paths=(),
        )
        return _publish_outcome(
            destination,
            payload,
            intended_exit=1,
            code=EvaluationErrorCode.INTERNAL_ERROR.value,
        )

    payload = _receipt_bytes(
        manifest_path=manifest_relative,
        execution_status="COMPLETED",
        decision_status="N/A",
        error_codes=(),
        invalid_resource_paths=(),
        dataset=dataset,
    )
    try:
        _publish_validated_destination(destination, payload)
    except EvaluationValidationError as error:
        return _emit_failure(error)
    except Exception:
        _emit_error(EvaluationErrorCode.INTERNAL_ERROR.value)
        return 1
    return 0
