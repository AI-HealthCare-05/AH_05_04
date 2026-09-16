"""Artifact-owner 계정으로 stdin 원문을 검증해 LOCAL_PRIVATE 최종 객체로 보존합니다."""

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata

_CHECKSUM = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArtifactFinalizerConfig:
    artifact_root: Path = field(repr=False)
    staging_root: Path = field(repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "ArtifactFinalizerConfig":
        forbidden = (
            "SOURCE_WRITER_PASSWORD",
            "SOURCE_CLEANUP_EXECUTOR_PASSWORD",
            "DB_PASSWORD",
            "DB_ADMIN_PASSWORD",
        )
        if any(env.get(key) for key in forbidden):
            raise ValueError("Artifact finalizer requires an isolated environment")
        artifact_value = env.get("SOURCE_ARTIFACT_LOCAL_ROOT", "")
        staging_value = env.get("SOURCE_ARTIFACT_FINALIZER_STAGING_ROOT", "")
        artifact_root = Path(artifact_value)
        staging_root = Path(staging_value)
        if (
            not artifact_value.strip()
            or not staging_value.strip()
            or not artifact_root.is_absolute()
            or not staging_root.is_absolute()
            or _paths_overlap(artifact_root, staging_root)
        ):
            raise ValueError("Artifact finalizer roots are invalid")
        _prepare_private_staging_root(staging_root)
        return cls(artifact_root=artifact_root, staging_root=staging_root)


def _prepare_private_staging_root(root: Path) -> None:
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("Artifact finalizer staging root cannot use symlinks")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        try:
            root_stat = root.lstat()
        except OSError:
            raise ValueError("Artifact finalizer staging root could not be inspected") from None
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != os.geteuid()
            or stat.S_IMODE(root_stat.st_mode) != 0o700
        ):
            raise ValueError("Artifact finalizer staging root must be owner-only") from None
    except OSError:
        raise ValueError("Artifact finalizer staging root could not be created") from None
    else:
        os.chmod(root, 0o700)


def _paths_overlap(left: Path, right: Path) -> bool:
    resolved_left = left.resolve(strict=False)
    resolved_right = right.resolve(strict=False)
    return (
        resolved_left == resolved_right
        or resolved_left.is_relative_to(resolved_right)
        or resolved_right.is_relative_to(resolved_left)
    )


def preserve_from_stdin(
    config: ArtifactFinalizerConfig,
    *,
    checksum: str,
    byte_size: int,
    content_type: str,
) -> str:
    metadata = RawArtifactMetadata(
        artifact_key="finalizer/stdin",
        raw_checksum=checksum,
        byte_size=byte_size,
        content_type=content_type,
    )
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".incoming-", dir=config.staging_root)
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        bytes_read = 0
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                read_size = min(_CHUNK_SIZE, byte_size - bytes_read + 1)
                chunk = sys.stdin.buffer.read(read_size)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > byte_size:
                    raise ValueError("Artifact finalizer input exceeds declared size")
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if bytes_read != byte_size:
            raise ValueError("Artifact finalizer input size mismatch")
        stored = LocalPrivateSourceArtifactStore(config.artifact_root).put_verified(
            page_number=1,
            file_path=temporary_path,
            metadata=metadata,
        )
        return stored.object_key
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checksum", required=True)
    parser.add_argument("--byte-size", required=True, type=int)
    parser.add_argument("--content-type", required=True)
    args = parser.parse_args()
    if _CHECKSUM.fullmatch(args.checksum) is None or args.byte_size < 0:
        print("Source artifact finalization rejected invalid metadata.", file=sys.stderr)
        return 2
    try:
        object_key = preserve_from_stdin(
            ArtifactFinalizerConfig.from_environment(os.environ),
            checksum=args.checksum,
            byte_size=args.byte_size,
            content_type=args.content_type,
        )
    except Exception:
        print("Source artifact finalization failed closed.", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "source-artifact-finalize@1",
                "storage_backend": "LOCAL_PRIVATE",
                "object_key": object_key,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
