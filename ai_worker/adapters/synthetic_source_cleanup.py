"""Isolated Local cleanup laboratory. Creates its own files; cannot attach to a Source store."""

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from uuid import uuid4

from ai_worker.tasks.rag.source_cleanup.execution import AuditEntry
from ai_worker.tasks.rag.source_cleanup.preflight import ApprovalEvidence, BatchTarget, ReviewBatch
from ai_worker.tasks.rag.source_cleanup.survey import ObjectObservation, ReferenceObservation, SurveyScope


def _read(fd: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected regular synthetic file")
        return stream.read()


def _write(fd: int, name: str, data: bytes) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)


class SyntheticJournal:
    """Append API + fsync, not OS-enforced immutability against the directory owner."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def _entries(self, extra: AuditEntry | None = None) -> list[AuditEntry]:
        content = _read(self._fd, "audit.jsonl")
        if content and not content.endswith(b"\n"):
            raise ValueError("Incomplete audit write")
        if extra is not None:
            content += json.dumps(asdict(extra)).encode() + b"\n"
        entries: list[AuditEntry] = []
        pending: dict[str, AuditEntry] = {}
        for line in content.splitlines():
            entry = AuditEntry(**json.loads(line))
            if entry.event == "INTENT":
                if entry.attempt_id in pending:
                    raise ValueError("Duplicate intent")
                pending[entry.attempt_id] = entry
            else:
                intent = pending.get(entry.attempt_id)
                if entry.event not in {"DELETED", "FAILED", "UNKNOWN", "BLOCKED"} or intent is None:
                    raise ValueError("Invalid audit sequence")
                if (intent.batch_hash, intent.object_ref) != (entry.batch_hash, entry.object_ref):
                    raise ValueError("Audit identity changed")
                # Keep the id reserved, but reject a second outcome for that attempt.
                if any(e.attempt_id == entry.attempt_id and e.event != "INTENT" for e in entries):
                    raise ValueError("Duplicate outcome")
            entries.append(entry)
        return entries

    def history(self, batch_hash: str, object_ref: str) -> tuple[AuditEntry, ...]:
        return tuple(e for e in self._entries() if e.batch_hash == batch_hash and e.object_ref == object_ref)

    def append(self, entry: AuditEntry) -> None:
        self._entries(entry)  # Validate both existing history and the proposed append.
        data = json.dumps(asdict(entry), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        descriptor = os.open("audit.jsonl", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=self._fd)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


class SyntheticCleanupLab:
    """Only generated ASCII fixture bytes. Cleanup explicitly disposes the entire test fixture."""

    def __init__(self, *, now: datetime, count: int = 2) -> None:
        if not 1 <= count <= 10 or now.tzinfo is None:
            raise ValueError("Invalid synthetic fixture")
        self._directory = TemporaryDirectory(prefix="source-cleanup-synthetic-")
        self._fd = os.open(self._directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._scope = SurveyScope(
            "synthetic-db-" + uuid4().hex,
            "synthetic-namespace-" + uuid4().hex,
            "LOCAL_PRIVATE",
            "source-artifact-retention-v1",
        )
        targets = []
        for index in range(count):
            key = f"{uuid4().hex}.artifact"
            payload = f"SYNTHETIC SOURCE FIXTURE {index}\n".encode()
            _write(self._fd, key, payload)
            metadata = os.stat(key, dir_fd=self._fd, follow_symlinks=False)
            generation = f"{metadata.st_dev}:{metadata.st_ino}:{metadata.st_mtime_ns}:{metadata.st_ctime_ns}"
            targets.append(
                BatchTarget(
                    ObjectObservation(
                        key, hashlib.sha256(payload).hexdigest(), len(payload), now - timedelta(days=31), True
                    ),
                    generation,
                    "RAW_RESPONSE" if index % 2 == 0 else "REJECTS",
                )
            )
        self.batch = ReviewBatch(self._scope, tuple(targets))
        # Fixture ages are declared synthetic facts, not real filesystem creation timestamps.
        fixture = {"batch_hash": self.batch.digest(), "targets": [asdict(t) for t in targets]}
        _write(self._fd, "fixture.json", json.dumps(fixture, default=str).encode())
        _write(self._fd, "audit.jsonl", b"")
        _write(self._fd, "lock", b"")
        self.approval = ApprovalEvidence(
            self.batch.digest(),
            self._scope.policy_version,
            "synthetic-receipt",
            "synthetic-pm",
            "synthetic-db-reviewer",
            "synthetic-executor",
            now - timedelta(hours=1),
            now + timedelta(hours=1),
        )
        self.reference_count = 0

    async def verify(self, *, batch_digest: str, executor: str) -> ApprovalEvidence | None:
        if self.approval and self.approval.batch_digest == batch_digest and self.approval.executor == executor:
            return self.approval
        return None

    @asynccontextmanager
    async def acquire(self, batch: ReviewBatch) -> AsyncIterator["_SyntheticSession"]:
        fixture = json.loads(_read(self._fd, "fixture.json"))
        if batch.digest() != fixture["batch_hash"] or batch != self.batch:
            raise ValueError("Foreign batch")
        lock = os.open("lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=self._fd)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            session = _SyntheticSession(self, lock)
            try:
                yield session
            finally:
                session.held = False
        finally:
            os.close(lock)

    def close(self) -> None:
        os.close(self._fd)
        self._directory.cleanup()


class _SyntheticSession:
    def __init__(self, lab: SyntheticCleanupLab, lock: int) -> None:
        self.lab = lab
        self.lock = lock
        self.held = True
        self.audit = SyntheticJournal(lab._fd)
        self.references = self

    async def assert_held(self) -> None:
        if not self.held:
            raise ValueError("Synthetic guard lost")
        os.fstat(self.lock)

    async def inspect_references(self, *, storage_backend: str, object_key: str) -> ReferenceObservation:
        await self.assert_held()
        if storage_backend != "LOCAL_PRIVATE" or object_key not in {
            t.observation.object_key for t in self.lab.batch.targets
        }:
            raise ValueError("Foreign object")
        return ReferenceObservation(
            self.lab._scope.database_id,
            self.lab.reference_count,
            self.lab._scope.namespace,
            True,
            0,
            True,
        )

    async def observe(self, target: BatchTarget) -> BatchTarget | None:
        await self.assert_held()
        if target not in self.lab.batch.targets:
            raise ValueError("Unknown synthetic target")
        key = target.observation.object_key
        try:
            before = os.stat(key, dir_fd=self.lab._fd, follow_symlinks=False)
            payload = _read(self.lab._fd, key)
            after = os.stat(key, dir_fd=self.lab._fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

        def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

        if not stat.S_ISREG(before.st_mode) or identity(before) != identity(after):
            raise ValueError("Synthetic object changed")
        generation = f"{after.st_dev}:{after.st_ino}:{after.st_mtime_ns}:{after.st_ctime_ns}"
        expected = target.observation
        return BatchTarget(
            ObjectObservation(key, hashlib.sha256(payload).hexdigest(), len(payload), expected.created_at, True),
            generation,
            target.artifact_kind,
        )

    async def delete(self, target: BatchTarget) -> None:
        if await self.observe(target) != target:
            raise ValueError("Synthetic object changed")
        await self.assert_held()
        os.unlink(target.observation.object_key, dir_fd=self.lab._fd)
        os.fsync(self.lab._fd)
