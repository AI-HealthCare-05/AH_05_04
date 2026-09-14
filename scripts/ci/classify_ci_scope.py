#!/usr/bin/env python3

import argparse
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath

SCOPE_NAMES = ("lint", "frontend", "migration", "backend", "contract", "worker")

COLLABORATION_TEMPLATE_PREFIX = ".github/ISSUE_TEMPLATE/"
COLLABORATION_TEMPLATE_FILES = {".github/PULL_REQUEST_TEMPLATE.md"}
MIGRATION_CONFIG_FILES = {"backend/alembic.ini"}

BACKEND_INTEGRATION_FILES = {
    "tests/integration/test_worker_ocr_persistence.py",
    "tests/integration/test_outbox_publisher.py",
    "tests/integration/test_worker_job_execution_repository.py",
    "tests/integration/test_worker_dlq_outbox_repository.py",
    "tests/integration/test_worker_recovery_repository.py",
}


@dataclass(frozen=True)
class CiScope:
    lint: bool = False
    frontend: bool = False
    migration: bool = False
    backend: bool = False
    contract: bool = False
    worker: bool = False

    @property
    def full(self) -> bool:
        return all(getattr(self, name) for name in SCOPE_NAMES)

    def enabled_names(self) -> set[str]:
        return {name for name in SCOPE_NAMES if getattr(self, name)}


def full_scope() -> CiScope:
    return CiScope(lint=True, frontend=True, migration=True, backend=True, contract=True, worker=True)


def _normalize_path(path: str) -> PurePosixPath:
    return PurePosixPath(path.replace("\\", "/").removeprefix("./"))


def _is_under(path: PurePosixPath, prefix: str) -> bool:
    root = PurePosixPath(prefix)
    return path == root or root in path.parents


def _scope_for_path(path: PurePosixPath) -> CiScope:
    value = path.as_posix()

    if value in COLLABORATION_TEMPLATE_FILES or value.startswith(COLLABORATION_TEMPLATE_PREFIX):
        return CiScope()
    if value.startswith("docs/") or ("/" not in value and value.endswith(".md")):
        return CiScope(contract=True)
    if _is_under(path, "frontend"):
        return CiScope(frontend=True, contract=True)
    if value in MIGRATION_CONFIG_FILES or _is_under(path, "backend/alembic") or _is_under(path, "tests/migration"):
        return CiScope(lint=True, migration=True, backend=True, contract=True)
    if _is_under(path, "backend"):
        return CiScope(lint=True, backend=True, contract=True)
    if _is_under(path, "ai_worker"):
        return CiScope(lint=True, contract=True, worker=True)
    if _is_under(path, "tests/contract") or _is_under(path, "tests/services"):
        return CiScope(lint=True, contract=True)
    if _is_under(path, "tests/integration/rag") or value in BACKEND_INTEGRATION_FILES:
        return CiScope(lint=True, backend=True, contract=True)
    return full_scope()


def _merge_scopes(scopes: Iterable[CiScope]) -> CiScope:
    values = list(scopes)
    return CiScope(**{field.name: any(getattr(scope, field.name) for scope in values) for field in fields(CiScope)})


def classify_paths(paths: Iterable[str]) -> CiScope:
    normalized = [_normalize_path(path) for path in paths if path]
    if not normalized:
        return full_scope()
    return _merge_scopes(_scope_for_path(path) for path in normalized)


def _changed_paths(base_sha: str, head_sha: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", f"{base_sha}...{head_sha}"],
        check=True,
        capture_output=True,
    )
    return [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]


def _write_github_output(path: Path, scope: CiScope) -> None:
    lines = [
        *(f"{name}={str(getattr(scope, name)).lower()}" for name in SCOPE_NAMES),
        f"full={str(scope.full).lower()}",
    ]
    with path.open("a", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify changed repository paths into CI test scopes.")
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--github-output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.full or args.event_name != "pull_request":
        scope = full_scope()
    else:
        if not args.base_sha or not args.head_sha:
            raise SystemExit("--base-sha and --head-sha are required for pull_request")
        scope = classify_paths(_changed_paths(args.base_sha, args.head_sha))
    _write_github_output(args.github_output, scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
