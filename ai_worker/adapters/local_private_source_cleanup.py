"""LOCAL_PRIVATE cleanup 요청·receipt와 executor 전용 삭제 adapter입니다."""

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from ai_worker.tasks.rag.source_cleanup.orphan_artifact import (
    ArtifactReferences,
    CleanupItemReceipt,
    CleanupReceipt,
    CleanupRequest,
    CleanupResult,
    CleanupTarget,
)

_SCHEMA = "source-artifact-cleanup@1"
_CHUNK = 1024 * 1024


class _LocalPrivateCleanupJournal:
    """비공개 root에 요청과 결과를 생성 전용 파일로 기록합니다."""

    def __init__(self, root: Path) -> None:
        self._root = _private_root(root)

    def append_request(self, request: CleanupRequest) -> None:
        payload: dict[str, object] = {
            "schema_version": _SCHEMA,
            "request_id": str(request.request_id),
            "run_group_key": request.run_group_key,
            "ingestion_run_id": str(request.ingestion_run_id) if request.ingestion_run_id else None,
            "targets": [
                {
                    "artifact_key": target.artifact_key,
                    "object_key": target.object_key,
                    "checksum": target.checksum,
                }
                for target in request.targets
            ],
            "failure_reason": request.failure_reason,
            "requested_at": request.requested_at.isoformat(),
        }
        self._append("requests", f"{request.request_id}.json", payload)

    def read_request(self, request_id: UUID) -> CleanupRequest:
        payload = self._read("requests", f"{request_id}.json")
        if (
            set(payload)
            != {
                "schema_version",
                "request_id",
                "run_group_key",
                "ingestion_run_id",
                "targets",
                "failure_reason",
                "requested_at",
            }
            or payload["schema_version"] != _SCHEMA
        ):
            raise ValueError("CLEANUP_REQUEST_INVALID")
        try:
            request_id_value = payload["request_id"]
            run_group_key = payload["run_group_key"]
            ingestion_run_id = payload["ingestion_run_id"]
            target_values = payload["targets"]
            failure_reason = payload["failure_reason"]
            requested_at = payload["requested_at"]
            if (
                not isinstance(request_id_value, str)
                or not isinstance(run_group_key, str)
                or (ingestion_run_id is not None and not isinstance(ingestion_run_id, str))
                or not isinstance(target_values, list)
                or not isinstance(failure_reason, str)
                or not isinstance(requested_at, str)
                or any(not isinstance(target, dict) for target in target_values)
            ):
                raise ValueError("CLEANUP_REQUEST_INVALID")
            targets = tuple(CleanupTarget(**cast(dict[str, str], target)) for target in target_values)
            return CleanupRequest(
                request_id=UUID(request_id_value),
                run_group_key=run_group_key,
                ingestion_run_id=UUID(ingestion_run_id) if ingestion_run_id else None,
                targets=targets,
                failure_reason=failure_reason,
                requested_at=datetime.fromisoformat(requested_at),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("CLEANUP_REQUEST_INVALID") from None

    def append_receipt(self, receipt: CleanupReceipt) -> None:
        payload: dict[str, object] = {
            "schema_version": _SCHEMA,
            "request_id": str(receipt.request_id),
            "executor": receipt.executor,
            "executed_at": receipt.executed_at.isoformat(),
            "items": [
                {
                    "artifact_key": item.artifact_key,
                    "object_key": item.object_key,
                    "checksum": item.checksum,
                    "references": {
                        "artifact_receipts": item.references.artifact_receipts,
                        "ingestion_runs": item.references.ingestion_runs,
                        "snapshot_members": item.references.snapshot_members,
                        "snapshots": item.references.snapshots,
                        "checksum_conflicts": item.references.checksum_conflicts,
                    },
                    "result": item.result.value,
                    "reason": item.reason,
                }
                for item in receipt.items
            ],
        }
        timestamp = receipt.executed_at.strftime("%Y%m%dT%H%M%S.%f%z")
        self._append("receipts", f"{receipt.request_id}-{timestamp}-{uuid4()}.json", payload)

    def read_receipts(self, request_id: UUID) -> tuple[CleanupReceipt, ...]:
        parent = self._directory("receipts", require_write=False)
        receipts = []
        for path in sorted(parent.glob(f"{request_id}-*.json")):
            payload = self._read("receipts", path.name)
            try:
                if payload.get("schema_version") != _SCHEMA or payload.get("request_id") != str(request_id):
                    raise ValueError("CLEANUP_RECEIPT_INVALID")
                actor = payload["executor"]
                executed_at = payload["executed_at"]
                item_values = payload["items"]
                if not isinstance(actor, str) or not isinstance(executed_at, str) or not isinstance(item_values, list):
                    raise ValueError("CLEANUP_RECEIPT_INVALID")
                items = tuple(self._parse_receipt_item(item) for item in item_values)
                receipts.append(CleanupReceipt(request_id, actor, datetime.fromisoformat(executed_at), items))
            except (KeyError, TypeError, ValueError):
                raise ValueError("CLEANUP_RECEIPT_INVALID") from None
        return tuple(receipts)

    @staticmethod
    def _parse_receipt_item(value: object) -> CleanupItemReceipt:
        if not isinstance(value, dict) or set(value) != {
            "artifact_key",
            "object_key",
            "checksum",
            "references",
            "result",
            "reason",
        }:
            raise ValueError("CLEANUP_RECEIPT_INVALID")
        references = value["references"]
        if not isinstance(references, dict) or set(references) != {
            "artifact_receipts",
            "ingestion_runs",
            "snapshot_members",
            "snapshots",
            "checksum_conflicts",
        }:
            raise ValueError("CLEANUP_RECEIPT_INVALID")
        return CleanupItemReceipt(
            artifact_key=cast(str, value["artifact_key"]),
            object_key=cast(str, value["object_key"]),
            checksum=cast(str, value["checksum"]),
            references=ArtifactReferences(**cast(dict[str, int], references)),
            result=CleanupResult(cast(str, value["result"])),
            reason=cast(str, value["reason"]),
        )

    def _append(self, directory: str, name: str, payload: Mapping[str, object]) -> None:
        parent = self._directory(directory)
        destination = parent / name
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        # Provisioned cleanup group may read requests/receipts; world access stays closed.
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o440)
            _sync(parent)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def _read(self, directory: str, name: str) -> dict[str, object]:
        path = self._directory(directory, require_write=False) / name
        try:
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if not stat.S_ISREG(info.st_mode) or mode & 0o337 or not mode & 0o440:
                raise ValueError("CLEANUP_JOURNAL_FILE_INVALID")
            with path.open("rb") as stream:
                raw = stream.read(64 * 1024 + 1)
            if not raw or len(raw) > 64 * 1024:
                raise ValueError("CLEANUP_JOURNAL_FILE_INVALID")
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            raise ValueError("CLEANUP_JOURNAL_FILE_INVALID") from None
        if not isinstance(value, dict):
            raise ValueError("CLEANUP_JOURNAL_FILE_INVALID")
        return value

    def _directory(self, name: str, *, require_write: bool = True) -> Path:
        path = self._root / name
        return _private_root(path, require_write=require_write)


class LocalPrivateCleanupRequestJournal:
    """writer에 노출하는 request append 전용 capability입니다."""

    def __init__(self, root: Path) -> None:
        self._journal = _LocalPrivateCleanupJournal(root)

    def append_request(self, request: CleanupRequest) -> None:
        self._journal.append_request(request)


class LocalPrivateCleanupExecutorJournal:
    """executor에 노출하는 request read·receipt append capability입니다."""

    def __init__(self, root: Path) -> None:
        self._journal = _LocalPrivateCleanupJournal(root)

    def read_request(self, request_id: UUID) -> CleanupRequest:
        return self._journal.read_request(request_id)

    def append_receipt(self, receipt: CleanupReceipt) -> None:
        self._journal.append_receipt(receipt)

    def read_receipts(self, request_id: UUID) -> tuple[CleanupReceipt, ...]:
        return self._journal.read_receipts(request_id)


class LocalPrivateArtifactCleanupExecutor:
    """writer에 주입하지 않는 삭제 전용 capability입니다."""

    def __init__(self, root: Path) -> None:
        self._root = _private_root(root)

    def exists(self, target: CleanupTarget) -> bool:
        path = self._path(target.object_key)
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        return True

    def delete_verified(self, target: CleanupTarget) -> None:
        path = self._path(target.object_key)
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ValueError("CLEANUP_ARTIFACT_MISSING") from None
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise ValueError("CLEANUP_ARTIFACT_INVALID")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
        if digest.hexdigest() != target.checksum:
            raise ValueError("CLEANUP_ARTIFACT_CHECKSUM_MISMATCH")
        path.unlink()
        _sync(path.parent)

    def _path(self, object_key: str) -> Path:
        path = self._root / object_key
        if not path.is_relative_to(self._root) or any(
            parent.is_symlink() for parent in path.parents if parent != self._root.parent
        ):
            raise ValueError("CLEANUP_OBJECT_OUTSIDE_ROOT")
        return path


def _private_root(root: Path, *, require_write: bool = True) -> Path:
    if not root.is_absolute() or any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("CLEANUP_ROOT_INVALID")
    if require_write:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    elif not root.is_dir():
        raise ValueError("CLEANUP_ROOT_INVALID")
    info = root.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or mode & 0o007
        or not os.access(root, os.R_OK | os.X_OK | (os.W_OK if require_write else 0))
    ):
        raise ValueError("CLEANUP_ROOT_INVALID")
    return root.resolve(strict=True)


def _sync(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
