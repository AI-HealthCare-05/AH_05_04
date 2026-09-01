from __future__ import annotations

import argparse
import errno
import os
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
_INTERNAL_ERROR_CODE = "EVAL_INTERNAL_ERROR"
_UNKNOWN_DATASET_CODE = "unknown-dataset"
_UNKNOWN_DATASET_VERSION = "0.0.0"


class _CliArgumentError(ValueError):
    pass


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


def _path_is_occupied(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _reject_symlink_chain(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        if candidate.is_symlink():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)


def _prepare_allowed_root(root: Path) -> Path:
    root = root.absolute()
    _reject_symlink_chain(root.parent)
    if _path_is_occupied(root):
        if root.is_symlink() or not root.is_dir():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
        return root
    try:
        root.mkdir(mode=0o755)
    except FileExistsError:
        if root.is_symlink() or not root.is_dir():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from None
    except OSError as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
    return root


def _validate_result_path(raw_path: str, *, allowed_root: Path, production: bool) -> Path:
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
    _reject_symlink_chain(destination.parent)
    if not destination.parent.is_dir() or destination.is_symlink():
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    return destination


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


def _entry_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _open_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _fsync_directory(directory_fd: int) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as error:
        unsupported = {errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)}
        if error.errno not in unsupported:
            raise


def _atomic_link(directory_fd: int, temporary_name: str, destination_name: str) -> None:
    try:
        os.link(
            temporary_name,
            destination_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileExistsError as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT) from error
    except (AttributeError, NotImplementedError, TypeError) as error:
        raise EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED) from error
    except OSError as error:
        unsupported = {
            errno.ENOSYS,
            errno.EXDEV,
            errno.EPERM,
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
        }
        if error.errno in unsupported:
            raise EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED) from error
        raise


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
    lock_owned: bool = False
    temporary_created: bool = False

    def acquire_lock(self) -> None:
        lock_payload = f"pid={os.getpid()}\ncreated_at={_utc_timestamp()}\n".encode("ascii")
        try:
            descriptor = os.open(self.lock_name, _open_flags(), 0o600, dir_fd=self.directory_fd)
        except FileExistsError as error:
            raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT) from error
        self.lock_owned = True
        _write_private_descriptor(descriptor, lock_payload)

    def write_temporary(self, payload: bytes) -> None:
        descriptor = os.open(self.temporary_name, _open_flags(), 0o600, dir_fd=self.directory_fd)
        self.temporary_created = True
        _write_private_descriptor(descriptor, payload)

    def cleanup(self) -> OSError | None:
        first_error: OSError | None = None
        names = (
            (self.temporary_name, self.temporary_created),
            (self.lock_name, self.lock_owned),
        )
        for name, owned in names:
            if not owned:
                continue
            try:
                os.unlink(name, dir_fd=self.directory_fd)
            except FileNotFoundError:
                pass
            except OSError as error:
                first_error = first_error or error
        try:
            os.close(self.directory_fd)
        except OSError as error:
            first_error = first_error or error
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
    os.unlink(files.temporary_name, dir_fd=files.directory_fd)
    files.temporary_created = False
    _fsync_directory(files.directory_fd)


def publish_receipt_no_clobber(destination: Path, payload: bytes) -> None:
    """Publish bytes through a private same-directory hard link without overwriting."""

    destination = destination.absolute()
    _reject_symlink_chain(destination.parent)
    if not destination.parent.is_dir() or destination.is_symlink():
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    if _path_is_occupied(destination) or _path_is_occupied(destination.with_name(f"{destination.name}.lock")):
        raise EvaluationValidationError(EvaluationErrorCode.RESULT_PATH_CONFLICT)

    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(destination.parent, directory_flags)
    except (NotImplementedError, TypeError) as error:
        raise EvaluationValidationError(EvaluationErrorCode.ATOMIC_PUBLISH_UNSUPPORTED) from error

    files = _PublishFiles(
        directory_fd=directory_fd,
        destination_name=destination.name,
        lock_name=f"{destination.name}.lock",
        temporary_name=f"{destination.name}.tmp.{uuid4()}",
    )
    primary_error: BaseException | None = None
    try:
        _publish_in_directory(files, payload)
    except BaseException as error:
        primary_error = error
    cleanup_error = files.cleanup()
    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error


def _emit_error(code: str) -> None:
    sys.stderr.write(f"{code}\n")


def _publish_outcome(destination: Path, payload: bytes, *, intended_exit: int, code: str) -> int:
    try:
        publish_receipt_no_clobber(destination, payload)
    except EvaluationValidationError as error:
        _emit_error(error.code.value)
        return 2 if error.code is EvaluationErrorCode.RESULT_PATH_CONFLICT else 1
    except Exception:
        _emit_error(_INTERNAL_ERROR_CODE)
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
        _emit_error(error.code.value)
        return 2

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
            error_codes=(_INTERNAL_ERROR_CODE,),
            invalid_resource_paths=(),
        )
        return _publish_outcome(
            destination,
            payload,
            intended_exit=1,
            code=_INTERNAL_ERROR_CODE,
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
        publish_receipt_no_clobber(destination, payload)
    except EvaluationValidationError as error:
        _emit_error(error.code.value)
        return 2 if error.code is EvaluationErrorCode.RESULT_PATH_CONFLICT else 1
    except Exception:
        _emit_error(_INTERNAL_ERROR_CODE)
        return 1
    return 0
