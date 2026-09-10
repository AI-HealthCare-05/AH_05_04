"""Durable #347 control adapters for an explicitly isolated Local synthetic database.

No application schema, Source Runtime configuration or production deletion entry point is changed.
Roles with INSERT approval/receipt privileges are separate from the execution role.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.source_cleanup.execution import AuditEntry
from ai_worker.tasks.rag.source_cleanup.preflight import ApprovalEvidence, BatchTarget, ReviewBatch
from ai_worker.tasks.rag.source_cleanup.survey import (
    Inventory,
    ObjectObservation,
    ReferenceObservation,
    SurveyResult,
    SurveyScope,
    survey_candidates,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import IngestionArtifactKind, RawArtifactMetadata

# Same lock for publication/reuse through reference commit and cleanup, independent of Source ID.
_LOCK_KEY = 347165335
_POLICY = "source-artifact-retention-v1"
_SAFE_ACTOR = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}")
_AUDIT_REASONS = {
    "INTENT": {"FINAL_CHECKS_PASSED"},
    "DELETED": {"DELETE_CONFIRMED"},
    "BLOCKED": {"FINAL_RECHECK_FAILED"},
    "UNKNOWN": {"DELETE_RESULT_UNKNOWN", "MISSING_REQUIRES_RECONCILIATION"},
}


async def require_synthetic_database(connection: AsyncConnection) -> str:
    url = connection.engine.url
    database = await connection.scalar(text("SELECT current_database()"))
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not isinstance(database, str)
        or not database.endswith("_cleanup347_test")
        or database != url.database
    ):
        raise ValueError("Explicit isolated cleanup test database required")
    return database


async def schema_fingerprint(connection: AsyncConnection) -> str:
    # A newly introduced table/column must not silently disappear from the reference inventory.
    rows = await connection.execute(
        text("""SELECT n.nspname, c.relname, a.attname,
                       pg_catalog.format_type(a.atttypid, a.atttypmod), a.attnotnull
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY n.nspname, c.relname, a.attnum""")
    )
    payload = json.dumps([list(row) for row in rows], separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


async def record_review(
    engine: AsyncEngine,
    *,
    batch_hash: str,
    role: str,
    executor: str,
    policy_version: str,
    valid_from: datetime,
    expires_at: datetime,
) -> int:
    """Append a review while excluding cleanup and other review changes for the batch."""
    if (
        re.fullmatch(r"[0-9a-f]{64}", batch_hash) is None
        or role not in {"PM", "DB_SECURITY"}
        or not _SAFE_ACTOR.fullmatch(executor)
        or policy_version != _POLICY
        or expires_at <= valid_from
    ):
        raise ValueError("Invalid review")
    async with engine.begin() as connection:
        await require_synthetic_database(connection)
        if not await connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(347, hashtext(:hash))"), {"hash": batch_hash}
        ):
            raise ValueError("Cleanup review busy")
        actor = await connection.scalar(text("SELECT current_user"))
        registered = await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM source_cleanup.reviewer WHERE actor=:actor AND role=:role)"),
            {"actor": actor, "role": role},
        )
        revoked = await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM source_cleanup.revocation WHERE batch_hash=:hash)"),
            {"hash": batch_hash},
        )
        if not registered or revoked:
            raise ValueError("Review actor unavailable or batch revoked")
        revision = await connection.scalar(
            text("""INSERT INTO source_cleanup.review
                (batch_hash,role,executor,policy_version,valid_from,expires_at)
                VALUES (:hash,:role,:executor,:policy,:start,:end) RETURNING revision"""),
            {
                "hash": batch_hash,
                "role": role,
                "executor": executor,
                "policy": policy_version,
                "start": valid_from,
                "end": expires_at,
            },
        )
        if not isinstance(revision, int):
            raise ValueError("Review revision unavailable")
        return revision


async def revoke_review_batch(engine: AsyncEngine, *, batch_hash: str) -> None:
    """Append a terminal batch revocation under the same lock used by cleanup."""
    if re.fullmatch(r"[0-9a-f]{64}", batch_hash) is None:
        raise ValueError("Invalid revocation digest")
    async with engine.begin() as connection:
        await require_synthetic_database(connection)
        if not await connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(347, hashtext(:hash))"), {"hash": batch_hash}
        ):
            raise ValueError("Cleanup review busy")
        actor = await connection.scalar(text("SELECT current_user"))
        if not await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM source_cleanup.reviewer WHERE actor=:actor)"), {"actor": actor}
        ):
            raise ValueError("Revocation actor unavailable")
        await connection.execute(
            text("INSERT INTO source_cleanup.revocation(batch_hash) VALUES (:hash)"), {"hash": batch_hash}
        )


def _root_identity(root: Path) -> str:
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("Invalid synthetic root")
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Invalid synthetic root permissions")
    return f"{info.st_dev}:{info.st_ino}"


def _generation(info: os.stat_result) -> str:
    return f"{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}:{info.st_ctime_ns}"


@asynccontextmanager
async def publication_transaction(engine: AsyncEngine, *, root: Path | None = None) -> AsyncIterator[AsyncConnection]:
    """Hold exclusion before writing/reusing bytes until the reference transaction commits.

    The managed synthetic workspace publisher must use this same transaction for reference INSERTs.
    Unmanaged production Source writers are deliberately not registered in this synthetic workflow.
    """
    lock = None
    try:
        async with engine.begin() as connection:
            await require_synthetic_database(connection)
            if not await connection.scalar(text("SELECT pg_try_advisory_xact_lock_shared(:key)"), {"key": _LOCK_KEY}):
                raise ValueError("Cleanup in progress")
            lock = _file_lock(root, shared=True) if root is not None else None
            yield connection
    finally:
        # Transaction commit/rollback completes before releasing the local publication lock.
        if lock is not None:
            os.close(lock)


def _file_lock(root: Path, *, shared: bool) -> int:
    _root_identity(root)
    fd = os.open(root / ".cleanup.lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Invalid workspace lock")
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


async def create_synthetic_workspace(
    engine: AsyncEngine, root: Path, *, count: int = 2, evaluation_offset_days: int = 0
) -> str:
    """Create a new private directory and generated bytes; never enroll existing Source objects.

    A durable receipt is written after actual Source-store publication. Failed receipt commits leave
    an unproven object, not a deletion candidate. Old filesystem timestamps are never inferred.
    """
    if type(evaluation_offset_days) is not int or evaluation_offset_days not in (0, 31):
        raise ValueError("Invalid synthetic clock offset")
    if type(count) is not int or not 1 <= count <= 10:
        raise ValueError("Invalid fixture count")
    async with publication_transaction(engine) as connection:
        database = await require_synthetic_database(connection)
        # Existing directories cannot be adopted as synthetic by placing a marker in them.
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        identity = _root_identity(root)
        lock_file = os.open(root / ".cleanup.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.fsync(lock_file)
        os.close(lock_file)
        workspace_id = str(uuid4())
        fingerprint = await schema_fingerprint(connection)
        await connection.execute(
            text("""INSERT INTO source_cleanup.workspace
                (id,database_name,root,root_identity,schema_hash,evaluation_offset_days) VALUES (:id,:db,:root,:identity,:schema,:offset)"""),
            {
                "id": workspace_id,
                "db": database,
                "root": str(root),
                "identity": identity,
                "schema": fingerprint,
                "offset": evaluation_offset_days,
            },
        )
        store = LocalPrivateSourceArtifactStore(root)
        for index in range(count):
            payload = f"SYNTHETIC SOURCE CLEANUP 347 {workspace_id} {index}\n".encode()
            source = root / f".fixture-{index}"
            source.write_bytes(payload)
            checksum = hashlib.sha256(payload).hexdigest()
            kind = IngestionArtifactKind.RAW_RESPONSE if index % 2 == 0 else IngestionArtifactKind.REJECTS
            try:
                stored = store.put_verified(
                    page_number=index + 1 if kind is IngestionArtifactKind.RAW_RESPONSE else None,
                    file_path=source,
                    metadata=RawArtifactMetadata(
                        artifact_key=f"synthetic-{index}",
                        raw_checksum=checksum,
                        byte_size=len(payload),
                        content_type="text/plain",
                    ),
                    artifact_kind=kind,
                    reject_code="SYNTHETIC_REJECT" if kind is IngestionArtifactKind.REJECTS else None,
                    parser_location="synthetic" if kind is IngestionArtifactKind.REJECTS else None,
                )
            finally:
                source.unlink()
            generation = _generation((root / stored.object_key).stat())
            await connection.execute(
                text("""INSERT INTO source_cleanup.object_receipt
                    (workspace_id,object_key,generation,checksum,byte_size,artifact_kind)
                    VALUES (:id,:key,:generation,:checksum,:size,:kind)"""),
                {
                    "id": workspace_id,
                    "key": stored.object_key,
                    "generation": generation,
                    "checksum": checksum,
                    "size": len(payload),
                    "kind": kind.value,
                },
            )
    # Only the fixture creator can register the exact batch-to-target mapping.
    # A failed registration leaves evidence in place but cannot authorize deletion.
    batch = await load_batch(engine, workspace_id)
    async with engine.begin() as connection:
        for target in batch.targets:
            key = target.observation.object_key
            await connection.execute(
                text("""INSERT INTO source_cleanup.batch_target
                (batch_hash,object_ref,workspace_id,object_key) VALUES (:hash,:ref,:id,:key)"""),
                {
                    "hash": batch.digest(),
                    "ref": hashlib.sha256(f"{batch.digest()}:{key}".encode()).hexdigest(),
                    "id": workspace_id,
                    "key": key,
                },
            )
    return workspace_id


async def load_batch(engine: AsyncEngine, workspace_id: str) -> ReviewBatch:
    async with engine.connect() as connection:
        database = await require_synthetic_database(connection)
        workspace = (
            (
                await connection.execute(
                    text("SELECT * FROM source_cleanup.workspace WHERE id=:id"), {"id": workspace_id}
                )
            )
            .mappings()
            .one()
        )
        if workspace["database_name"] != database:
            raise ValueError("Database binding changed")
        rows = (
            await connection.execute(
                text("SELECT * FROM source_cleanup.object_receipt WHERE workspace_id=:id ORDER BY object_key"),
                {"id": workspace_id},
            )
        ).mappings()
        targets = tuple(
            BatchTarget(
                ObjectObservation(r["object_key"], r["checksum"], r["byte_size"], r["created_at"], True),
                r["generation"],
                r["artifact_kind"],
            )
            for r in rows
        )
        return ReviewBatch(SurveyScope(workspace_id, workspace["root"], "LOCAL_PRIVATE", _POLICY), targets)


class PostgresApprovalVerifier:
    def __init__(self, engine: AsyncEngine, *, pm_role: str, db_security_role: str) -> None:
        if pm_role == db_security_role or not all(_SAFE_ACTOR.fullmatch(s) for s in (pm_role, db_security_role)):
            raise ValueError("Distinct trusted review roles required")
        self.engine = engine
        self.pm_role = pm_role
        self.db_security_role = db_security_role

    async def verify(self, *, batch_digest: str, executor: str) -> ApprovalEvidence | None:
        async with self.engine.connect() as connection:
            await require_synthetic_database(connection)
            rows = (
                (
                    await connection.execute(
                        text("""SELECT DISTINCT ON (role) * FROM source_cleanup.review
                WHERE batch_hash=:hash AND NOT EXISTS
                  (SELECT 1 FROM source_cleanup.revocation WHERE batch_hash=:hash)
                ORDER BY role, revision DESC"""),
                        {"hash": batch_digest},
                    )
                )
                .mappings()
                .all()
            )
            by_role = {r["role"]: r for r in rows}
            if set(by_role) != {"PM", "DB_SECURITY"}:
                return None
            pm, security = by_role["PM"], by_role["DB_SECURITY"]
            if (
                security["revision"] >= pm["revision"]
                or pm["actor"] != self.pm_role
                or security["actor"] != self.db_security_role
                or any(r["executor"] != executor or r["policy_version"] != _POLICY for r in rows)
                or executor in {self.pm_role, self.db_security_role}
            ):
                return None
            return ApprovalEvidence(
                batch_digest,
                _POLICY,
                hashlib.sha256(f"{batch_digest}:{pm['revision']}:{security['revision']}".encode()).hexdigest(),
                pm["actor"],
                security["actor"],
                executor,
                max(r["valid_from"] for r in rows),
                min(r["expires_at"] for r in rows),
            )


class PostgresAuditJournal:
    """Every append commits on a separate connection before returning, including before unlink."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def history(self, batch_hash: str, object_ref: str) -> tuple[AuditEntry, ...]:
        async with self.engine.connect() as connection:
            await require_synthetic_database(connection)
            rows = await connection.scalars(
                text("""SELECT payload FROM source_cleanup.audit
                WHERE batch_hash=:hash AND object_ref=:ref ORDER BY sequence"""),
                {"hash": batch_hash, "ref": object_ref},
            )
            return tuple(AuditEntry(**r) for r in rows)

    async def _intent_payload(
        self, connection: AsyncConnection, entry: AuditEntry, caller: str, target: RowMapping
    ) -> dict[str, object]:
        if not await connection.scalar(
            text("SELECT pg_try_advisory_xact_lock_shared(347, hashtext(:hash))"), {"hash": entry.batch_hash}
        ):
            raise ValueError("Cleanup review busy")
        rows = (
            (
                await connection.execute(
                    text("""SELECT DISTINCT ON (r.role) r.*,
                    EXISTS (SELECT 1 FROM source_cleanup.reviewer v
                            WHERE v.actor=r.actor AND v.role=r.role) AS registered
                    FROM source_cleanup.review r WHERE r.batch_hash=:hash
                    ORDER BY r.role,r.revision DESC"""),
                    {"hash": entry.batch_hash},
                )
            )
            .mappings()
            .all()
        )
        by_role = {row["role"]: row for row in rows}
        if set(by_role) != {"PM", "DB_SECURITY"}:
            raise ValueError("Audit approval invalid")
        pm, security = by_role["PM"], by_role["DB_SECURITY"]
        revoked = await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM source_cleanup.revocation WHERE batch_hash=:hash)"),
            {"hash": entry.batch_hash},
        )
        effective_time = target["effective_time"]
        if (
            security["revision"] >= pm["revision"]
            or pm["executor"] != caller
            or security["executor"] != caller
            or pm["actor"] == caller
            or security["actor"] == caller
            or not pm["registered"]
            or not security["registered"]
            or effective_time < max(pm["valid_from"], security["valid_from"])
            or effective_time >= min(pm["expires_at"], security["expires_at"])
            or revoked
        ):
            raise ValueError("Audit approval invalid")
        referenced = await connection.scalar(
            text("""SELECT EXISTS (SELECT 1 FROM public.rag_source_ingestion_artifact
            WHERE storage_backend='LOCAL_PRIVATE' AND object_key=:key)"""),
            {"key": target["object_key"]},
        )
        if effective_time <= target["created_at"] + timedelta(days=30) or referenced:
            raise ValueError("Audit reference invalid")
        return {
            "batch_hash": entry.batch_hash,
            "object_ref": entry.object_ref,
            "attempt_id": entry.attempt_id,
            "checksum": target["checksum"],
            "artifact_kind": target["artifact_kind"],
            "policy_version": pm["policy_version"],
            "receipt_id": hashlib.sha256(
                f"{entry.batch_hash}:{pm['revision']}:{security['revision']}".encode()
            ).hexdigest(),
            "pm_actor": pm["actor"],
            "db_security_actor": security["actor"],
            "executor": caller,
            "references_verified": True,
        }

    async def _outcome_payload(
        self, connection: AsyncConnection, entry: AuditEntry, caller: str
    ) -> tuple[dict[str, object], int]:
        intent = (
            (
                await connection.execute(
                    text("""SELECT sequence,payload,recorded_by,batch_hash,object_ref
                    FROM source_cleanup.audit WHERE attempt_id=:attempt AND event='INTENT'"""),
                    {"attempt": entry.attempt_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            intent is None
            or intent["batch_hash"] != entry.batch_hash
            or intent["object_ref"] != entry.object_ref
            or intent["recorded_by"] != caller
        ):
            raise ValueError("Audit sequence invalid")
        return dict(intent["payload"]), intent["sequence"]

    async def append(self, entry: AuditEntry) -> None:
        if (
            not all(
                re.fullmatch(r"[0-9a-f]{64}", value) for value in (entry.batch_hash, entry.object_ref, entry.checksum)
            )
            or entry.reason not in _AUDIT_REASONS.get(entry.event, set())
            or entry.artifact_kind not in {"RAW_RESPONSE", "REJECTS"}
            or entry.policy_version != _POLICY
            or not all(_SAFE_ACTOR.fullmatch(s) for s in (entry.pm_actor, entry.db_security_actor, entry.executor))
        ):
            raise ValueError("Invalid redacted audit payload")
        try:
            UUID(entry.attempt_id)
        except ValueError:
            raise ValueError("Invalid redacted audit payload") from None
        async with self.engine.begin() as connection:
            await require_synthetic_database(connection)
            caller = await connection.scalar(text("SELECT current_user"))
            if caller != entry.executor:
                raise ValueError("Audit executor mismatch")
            target = (
                (
                    await connection.execute(
                        text("""WITH observed AS (SELECT clock_timestamp() AS recorded_time)
                        SELECT r.checksum,r.artifact_kind,r.created_at,w.evaluation_offset_days,w.database_name,
                               b.object_key,observed.recorded_time,
                               observed.recorded_time + make_interval(days=>w.evaluation_offset_days) AS effective_time
                        FROM source_cleanup.batch_target b
                        JOIN source_cleanup.object_receipt r USING (workspace_id,object_key)
                        JOIN source_cleanup.workspace w ON w.id=b.workspace_id
                        CROSS JOIN observed
                        WHERE b.batch_hash=:hash AND b.object_ref=:ref"""),
                        {"hash": entry.batch_hash, "ref": entry.object_ref},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if target is None or target["database_name"] != self.engine.url.database:
                raise ValueError("Audit target invalid")

            if entry.event == "INTENT":
                payload = await self._intent_payload(connection, entry, caller, target)
                intent_sequence = None
            else:
                payload, intent_sequence = await self._outcome_payload(connection, entry, caller)
            recorded_time = target["recorded_time"]
            payload.update(event=entry.event, reason=entry.reason, occurred_at=recorded_time.isoformat())
            await connection.execute(
                text("""INSERT INTO source_cleanup.audit
                (batch_hash,object_ref,attempt_id,event,payload,intent_sequence,recorded_at)
                VALUES (:hash,:ref,:attempt,:event,CAST(:payload AS jsonb),:intent,:recorded)"""),
                {
                    "hash": entry.batch_hash,
                    "ref": entry.object_ref,
                    "attempt": entry.attempt_id,
                    "event": entry.event,
                    "payload": json.dumps(payload, separators=(",", ":")),
                    "intent": intent_sequence,
                    "recorded": recorded_time,
                },
            )


class PostgresLocalCleanupGuard:
    def __init__(self, engine: AsyncEngine, audit: PostgresAuditJournal) -> None:
        self.engine = engine
        self.audit = audit

    @asynccontextmanager
    async def acquire(self, batch: ReviewBatch) -> AsyncIterator["_LocalSession"]:
        async with self.engine.begin() as connection:
            await require_synthetic_database(connection)
            unsafe = await connection.scalar(
                text("""SELECT
                (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user)
                OR has_any_column_privilege(current_user,'source_cleanup.review','INSERT')
                OR has_any_column_privilege(current_user,'source_cleanup.object_receipt','INSERT')
                OR has_any_column_privilege(current_user,'source_cleanup.batch_target','INSERT')
                OR has_any_column_privilege(current_user,'source_cleanup.reviewer','INSERT')
                OR has_table_privilege(current_user,'source_cleanup.audit','UPDATE,DELETE,TRUNCATE')""")
            )
            if unsafe:
                raise ValueError("Separate restricted execution role required")
            if not await connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}):
                raise ValueError("Source publication in progress")
            # Python review/revocation commands take the exclusive side of this lock.
            # Hold through final verification, unlink and transaction exit.
            if not await connection.scalar(
                text("SELECT pg_try_advisory_xact_lock_shared(347, hashtext(:hash))"),
                {"hash": batch.digest()},
            ):
                raise ValueError("Batch review change in progress")
            # Every supported publisher holds the shared global lock through reference commit.
            row = (
                (
                    await connection.execute(
                        text("SELECT * FROM source_cleanup.workspace WHERE id=:id"), {"id": batch.scope.database_id}
                    )
                )
                .mappings()
                .one()
            )
            if (
                row["root"] != batch.scope.namespace
                or row["database_name"] != self.engine.url.database
                or row["schema_hash"] != await schema_fingerprint(connection)
                or row["root_identity"] != _root_identity(Path(row["root"]))
                or batch != await load_batch(self.engine, batch.scope.database_id)
            ):
                raise ValueError("Workspace or schema binding changed")
            lock = _file_lock(Path(row["root"]), shared=False)
            try:
                fd = os.open(row["root"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except BaseException:
                os.close(lock)
                raise
            session = _LocalSession(connection, batch, fd, self.audit, row["schema_hash"], lock)
            try:
                yield session
                await session.assert_held()
            finally:
                session.held = False
                os.close(fd)
                os.close(lock)


class _LocalSession:
    def __init__(
        self,
        connection: AsyncConnection,
        batch: ReviewBatch,
        fd: int,
        audit: PostgresAuditJournal,
        schema_hash: str,
        lock: int,
    ) -> None:
        self.connection = connection
        self.batch = batch
        self.fd = fd
        self.audit = audit
        self.references = self
        self.held = True
        self.schema_hash = schema_hash
        self.lock = lock

    async def assert_held(self) -> None:
        if not self.held or self.connection.closed or not self.connection.in_transaction():
            raise ValueError("Cleanup lock lost")
        await self.connection.execute(text("SELECT 1"))
        if await schema_fingerprint(self.connection) != self.schema_hash:
            raise ValueError("Reference schema changed")
        actual = os.fstat(self.fd)
        lock_info = os.fstat(self.lock)
        lock_link = os.stat(".cleanup.lock", dir_fd=self.fd, follow_symlinks=False)
        if (lock_info.st_dev, lock_info.st_ino) != (lock_link.st_dev, lock_link.st_ino):
            raise ValueError("Workspace lock replaced")
        if _root_identity(Path(self.batch.scope.namespace)) != f"{actual.st_dev}:{actual.st_ino}":
            raise ValueError("Root replaced")

    async def inspect_references(self, *, storage_backend: str, object_key: str) -> ReferenceObservation:
        await self.assert_held()
        if storage_backend != "LOCAL_PRIVATE" or object_key not in {
            t.observation.object_key for t in self.batch.targets
        }:
            raise ValueError("Foreign object")
        count = await self.connection.scalar(
            text("""SELECT count(*) FROM public.rag_source_ingestion_artifact
            WHERE storage_backend=:backend AND object_key=:key"""),
            {"backend": storage_backend, "key": object_key},
        )
        scope = await self._inspect_downstream_scope(object_key)
        return replace(scope, direct_count=count)

    async def _inspect_downstream_scope(self, object_key: str) -> ReferenceObservation:
        """Only the registered, generated synthetic workspace has a closed inventory.

        Operational Citation/Evaluation/external evidence discovery is not implemented.
        Extending the database gate alone must never turn that missing survey into zero.
        """
        try:
            if self.batch.environment != "SYNTHETIC_LOCAL" or self.batch.scope.storage_backend != "LOCAL_PRIVATE":
                raise ValueError("Non-synthetic environment")
            await require_synthetic_database(self.connection)
            await self.assert_held()
            registered = await load_batch(self.connection.engine, self.batch.scope.database_id)
            target = next(t for t in self.batch.targets if t.observation.object_key == object_key)
            if registered != self.batch or not target.observation.source_owned or await self.observe(target) != target:
                raise ValueError("Synthetic provenance unproven")
        except (ValueError, StopIteration, OSError) as exc:
            raise NotImplementedError("Non-synthetic downstream reference survey unavailable") from exc
        return ReferenceObservation(self.batch.scope.database_id, None, self.batch.scope.namespace, True, 0, True)

    def _parent(self, key: str) -> tuple[int, str]:
        match = re.fullmatch(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})\.artifact", key)
        if not match or match[1] != match[2][:2]:
            raise ValueError("Invalid object key")
        first = os.open("sha256", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            parent = os.open(match[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=first)
        finally:
            os.close(first)
        return parent, f"{match[2]}.artifact"

    async def observe(self, target: BatchTarget) -> BatchTarget | None:
        await self.assert_held()
        if target not in self.batch.targets:
            raise ValueError("Unknown target")
        parent, name = self._parent(target.observation.object_key)
        try:
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                return None
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size != target.observation.byte_size:
                    raise ValueError("Object changed")
                payload = stream.read(target.observation.byte_size + 1)
                after = os.fstat(stream.fileno())
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                _generation(before) != _generation(after)
                or _generation(after) != _generation(linked)
                or not payload.startswith(f"SYNTHETIC SOURCE CLEANUP 347 {self.batch.scope.database_id} ".encode())
            ):
                raise ValueError("Synthetic object changed")
            return BatchTarget(
                ObjectObservation(
                    target.observation.object_key,
                    hashlib.sha256(payload).hexdigest(),
                    len(payload),
                    target.observation.created_at,
                    True,
                ),
                _generation(after),
                target.artifact_kind,
            )
        finally:
            os.close(parent)

    async def delete(self, target: BatchTarget) -> None:
        if await self.observe(target) != target:
            raise ValueError("Object changed")
        await self.assert_held()
        parent, name = self._parent(target.observation.object_key)
        try:
            if _generation(os.stat(name, dir_fd=parent, follow_symlinks=False)) != target.generation:
                raise ValueError("Object replaced")
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)


class _ReceiptInventory:
    def __init__(self, batch: ReviewBatch) -> None:
        self.batch = batch

    def read_inventory(self) -> Inventory:
        return Inventory(self.batch.scope.namespace, tuple(t.observation for t in self.batch.targets), True)


async def survey_workspace(engine: AsyncEngine, batch: ReviewBatch, *, now: datetime) -> SurveyResult:
    """Read actual bytes and immutable receipts under publication exclusion; no approval/write/delete."""
    guard = PostgresLocalCleanupGuard(engine, PostgresAuditJournal(engine))
    try:
        async with guard.acquire(batch) as session:
            for target in batch.targets:
                if await session.observe(target) != target:
                    return SurveyResult((), False, "OBJECT_RECEIPT_MISMATCH")
            return await survey_candidates(
                scope=batch.scope, now=now, inventory=_ReceiptInventory(batch), references=session.references
            )
    except Exception:
        return SurveyResult((), False, "WORKSPACE_EVIDENCE_UNAVAILABLE")


@asynccontextmanager
async def reference_existing_objects(engine: AsyncEngine, batch: ReviewBatch) -> AsyncIterator[AsyncConnection]:
    """Managed synthetic writer: validate reused objects before creating references, through commit.

    After cleanup has removed an object, a later writer must not create a dangling reference from an
    old StoredRawArtifact. A fresh publication needs a new workspace/receipt and a new review batch.
    """
    root = Path(batch.scope.namespace)
    async with publication_transaction(engine, root=root) as connection:
        if batch != await load_batch(engine, batch.scope.database_id):
            raise ValueError("Publication binding changed")
        row = (
            (
                await connection.execute(
                    text("SELECT * FROM source_cleanup.workspace WHERE id=:id"), {"id": batch.scope.database_id}
                )
            )
            .mappings()
            .one()
        )
        if row["root_identity"] != _root_identity(root):
            raise ValueError("Publication root changed")
        lock = _file_lock(root, shared=True)
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            session = _LocalSession(connection, batch, fd, PostgresAuditJournal(engine), row["schema_hash"], lock)
            try:
                for target in batch.targets:
                    if await session.observe(target) != target:
                        raise ValueError("Publication object unavailable")
                yield connection
            finally:
                session.held = False
                os.close(fd)
        finally:
            os.close(lock)
