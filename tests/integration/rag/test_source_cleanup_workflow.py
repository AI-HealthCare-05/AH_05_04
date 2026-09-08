"""Durable Local/real PostgreSQL workflow, restricted to an explicit disposable test database."""

import asyncio
import json
import os
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_source_cleanup_references import add_reference, seed_run

from ai_worker.adapters.postgresql_source_cleanup import (
    PostgresApprovalVerifier,
    PostgresAuditJournal,
    PostgresLocalCleanupGuard,
    create_synthetic_workspace,
    load_batch,
    publication_transaction,
    reference_existing_objects,
    require_synthetic_database,
    survey_workspace,
)
from ai_worker.tasks.rag.source_cleanup.execution import execute_synthetic_batch
from app.models.rag_source import RagIngestionRunStatus

DATABASE_URL = os.environ.get("SOURCE_CLEANUP_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not DATABASE_URL, reason="Explicit disposable DB required")]


@pytest_asyncio.fixture(scope="module")
async def control():
    admin = create_async_engine(DATABASE_URL, poolclass=NullPool)
    suffix = uuid4().hex[:10]
    roles = {key: f"cleanup347_{key}_{suffix}" for key in ("pm", "security", "executor")}
    engines = {}
    async with admin.begin() as connection:
        await require_synthetic_database(connection)
        raw = await connection.get_raw_connection()
        script = Path("tools/source_cleanup/synthetic_control.sql").read_text()
        if not await connection.scalar(text("SELECT to_regnamespace('source_cleanup')")):
            await raw.driver_connection.execute(script)
        for key, role in roles.items():
            password = uuid4().hex
            await connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
            url = make_url(DATABASE_URL).set(username=role, password=password)
            engines[key] = create_async_engine(url, poolclass=NullPool)
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public, source_cleanup TO {role}"))
            await connection.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public, source_cleanup TO {role}"))
        for key in ("pm", "security"):
            role = roles[key]
            # Actor column is intentionally not grantable to a reviewer.
            await connection.execute(
                text(f"""GRANT INSERT
                (batch_hash,role,executor,policy_version,valid_from,expires_at)
                ON source_cleanup.review TO {role}""")
            )
            await connection.execute(text(f"GRANT INSERT (batch_hash) ON source_cleanup.revocation TO {role}"))
        executor = roles["executor"]
        await connection.execute(text(f"GRANT EXECUTE ON FUNCTION source_cleanup.lock_references() TO {executor}"))
        await connection.execute(
            text(f"GRANT INSERT (batch_hash,object_ref,attempt_id,event,payload) ON source_cleanup.audit TO {executor}")
        )
        await connection.execute(text(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA source_cleanup TO {executor}"))
    try:
        yield admin, engines, roles
    finally:
        for engine in engines.values():
            await engine.dispose()
        # Whole disposable DB is removed after the test run; do not drop immutable audit evidence here.
        await admin.dispose()


@pytest_asyncio.fixture
async def workflow(control, tmp_path):
    admin, engines, roles = control
    root = tmp_path.resolve() / "source"
    workspace = await create_synthetic_workspace(admin, root)
    batch = await load_batch(engines["executor"], workspace)
    # Simulate elapsed time, without backdating physical metadata or the DB publication receipt.
    now = max(t.observation.created_at for t in batch.targets) + timedelta(days=31)
    verifier = PostgresApprovalVerifier(engines["executor"], pm_role=roles["pm"], db_security_role=roles["security"])
    journal = PostgresAuditJournal(engines["executor"])
    guard = PostgresLocalCleanupGuard(engines["executor"], journal)
    return admin, engines, roles, root, batch, now, verifier, journal, guard


async def approve(workflow, *, expires=None):
    _, engines, roles, _, batch, now, *_ = workflow
    for key, role in (("pm", "PM"), ("security", "DB_SECURITY")):
        async with engines[key].begin() as connection:
            await connection.execute(
                text("""INSERT INTO source_cleanup.review
                (batch_hash,role,executor,policy_version,valid_from,expires_at)
                VALUES (:hash,:role,:executor,:policy,:start,:end)"""),
                {
                    "hash": batch.digest(),
                    "role": role,
                    "executor": roles["executor"],
                    "policy": batch.scope.policy_version,
                    "start": now - timedelta(hours=1),
                    "end": expires or now + timedelta(hours=1),
                },
            )


async def execute(workflow, **kwargs):
    _, _, roles, _, batch, now, verifier, _, guard = workflow
    return await execute_synthetic_batch(
        batch=batch, executor=roles["executor"], clock=lambda: now, approvals=verifier, guard=guard, **kwargs
    )


async def test_persistent_flow_and_reopen_skips_success(workflow):
    _, engines, roles, root, batch, now, _, _, _ = workflow
    denied = await execute(workflow)
    assert not denied.complete
    assert all((root / t.observation.object_key).exists() for t in batch.targets)
    await approve(workflow)
    async with workflow[-1].acquire(batch) as locked:
        await locked.assert_held()
    result = await execute(workflow)
    assert result.complete
    assert all(not (root / t.observation.object_key).exists() for t in batch.targets)
    # Reconstruct every adapter and reload batch from DB, not the original in-memory lab.
    reloaded = await load_batch(engines["executor"], batch.scope.database_id)
    assert reloaded == batch
    result = await execute_synthetic_batch(
        batch=reloaded,
        executor=roles["executor"],
        clock=lambda: now,
        approvals=PostgresApprovalVerifier(
            engines["executor"], pm_role=roles["pm"], db_security_role=roles["security"]
        ),
        guard=PostgresLocalCleanupGuard(engines["executor"], PostgresAuditJournal(engines["executor"])),
    )
    assert result.complete and all(i.reason == "ALREADY_RECORDED" for i in result.items)
    async with engines["executor"].connect() as connection:
        payloads = (
            await connection.scalars(
                text("SELECT payload::text FROM source_cleanup.audit WHERE batch_hash=:hash"), {"hash": batch.digest()}
            )
        ).all()
    assert len(payloads) == 4
    assert not any(str(root) in p or "SYNTHETIC SOURCE CLEANUP" in p or "sha256/" in p for p in payloads)


@pytest.mark.parametrize("status", [RagIngestionRunStatus.FAILED, RagIngestionRunStatus.NO_CHANGE])
async def test_actual_source_references_protect_after_approval(workflow, status):
    admin, _, _, root, batch, *_ = workflow
    await approve(workflow)
    async with publication_transaction(admin) as connection, AsyncSession(bind=connection) as session:
        run = await seed_run(session, status)
        await add_reference(session, run, batch.targets[0].observation.object_key)
    result = await execute(workflow)
    assert not result.complete
    assert (root / batch.targets[0].observation.object_key).exists()


async def test_inflight_publication_and_new_writer_excluded(workflow):
    admin, _, _, _, batch, _, _, _, guard = workflow
    await approve(workflow)
    async with publication_transaction(admin):
        assert not (await execute(workflow)).complete
    async with guard.acquire(batch):
        with pytest.raises(ValueError, match="Cleanup in progress"):
            async with publication_transaction(admin):
                pytest.fail("writer entered cleanup guard")
    assert (await execute(workflow)).complete


async def test_uncommitted_direct_reference_blocks_cleanup(workflow):
    admin, _, _, root, batch, *_ = workflow
    await approve(workflow)
    async with AsyncSession(admin) as writer:
        run = await seed_run(writer)
        await add_reference(writer, run, batch.targets[0].observation.object_key)
        assert not (await execute(workflow)).complete
    assert all((root / t.observation.object_key).exists() for t in batch.targets)


@pytest.mark.parametrize("change", ["revoke", "expire", "replace", "schema", "wrong_scope"])
async def test_changed_evidence_blocks(workflow, change):
    admin, engines, _, root, batch, now, *_ = workflow
    await approve(workflow, expires=now if change == "expire" else None)
    if change == "revoke":
        async with engines["pm"].begin() as connection:
            await connection.execute(
                text("INSERT INTO source_cleanup.revocation(batch_hash) VALUES (:hash)"), {"hash": batch.digest()}
            )
    elif change == "replace":
        path = root / batch.targets[0].observation.object_key
        payload = path.read_bytes()
        path.unlink()
        path.write_bytes(payload)
    elif change == "schema":
        async with admin.begin() as connection:
            await connection.execute(text("CREATE TABLE public.synthetic_new_reference (object_key text)"))
    elif change == "wrong_scope":
        workflow = (
            *workflow[:4],
            replace(batch, scope=replace(batch.scope, namespace=str(root.parent))),
            *workflow[5:],
        )
    try:
        assert not (await execute(workflow)).complete
        assert all((root / t.observation.object_key).exists() for t in batch.targets)
    finally:
        if change == "schema":
            async with admin.begin() as connection:
                await connection.execute(text("DROP TABLE public.synthetic_new_reference"))


async def test_audit_is_immutable_and_executor_cannot_approve(workflow):
    admin, engines, _, _, batch, *_ = workflow
    await approve(workflow)
    assert (await execute(workflow)).complete
    statements = [
        "UPDATE source_cleanup.audit SET event='FAILED'",
        "DELETE FROM source_cleanup.audit",
        "TRUNCATE source_cleanup.audit",
        "INSERT INTO source_cleanup.revocation(batch_hash) VALUES ('forged')",
        "INSERT INTO source_cleanup.review(batch_hash,role,actor,executor,policy_version,valid_from,expires_at) VALUES ('x','PM','forged','forged','source-artifact-retention-v1',now(),now()+interval '1 hour')",
    ]
    for statement in statements:
        async with engines["executor"].begin() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text(statement))
    # Even a regular owner UPDATE/DELETE/TRUNCATE is rejected by the trigger.
    # A superuser explicitly disabling triggers remains outside the protection claim.
    for statement in statements[:3]:
        async with admin.begin() as connection:
            with pytest.raises(DBAPIError, match="CLEANUP_APPEND_ONLY"):
                await connection.execute(text(statement))
    async with engines["pm"].begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(text("UPDATE source_cleanup.review SET executor='forged'"))


async def test_audit_unavailable_never_deletes(workflow):
    _, engines, roles, root, batch, *_ = workflow
    await approve(workflow)
    # Use PM's read-only audit permissions to force INTENT failure before unlink.
    journal = PostgresAuditJournal(engines["pm"])
    changed = (*workflow[:7], journal, PostgresLocalCleanupGuard(engines["executor"], journal))
    result = await execute(changed)
    assert not result.complete and result.reason == "EXECUTION_OR_AUDIT_UNAVAILABLE"
    assert all((root / t.observation.object_key).exists() for t in batch.targets)


async def test_lost_result_persists_intent_and_reopen_is_unknown(workflow, monkeypatch):
    _, _, _, root, batch, _, _, journal, _ = workflow
    await approve(workflow)
    append = journal.append

    async def fail_after_intent(entry):
        if entry.event == "DELETED":
            raise RuntimeError("synthetic audit outage")
        await append(entry)

    monkeypatch.setattr(journal, "append", fail_after_intent)
    assert not (await execute(workflow)).complete
    monkeypatch.setattr(journal, "append", append)
    result = await execute(workflow, retry=True, max_attempts=2)
    assert not result.complete
    assert result.items[0].outcome == "UNKNOWN"
    assert result.items[0].reason == "MISSING_REQUIRES_RECONCILIATION"
    assert not (root / batch.targets[0].observation.object_key).exists()


async def test_no_adoption_of_existing_objects(workflow):
    admin, _, _, root, *_ = workflow
    with pytest.raises(FileExistsError):
        await create_synthetic_workspace(admin, root)


async def test_source_writer_waits_for_reference_commit(workflow):
    admin, _, _, root, batch, *_ = workflow
    await approve(workflow)
    ready, release = asyncio.Event(), asyncio.Event()

    async def writer():
        async with reference_existing_objects(admin, batch) as connection, AsyncSession(bind=connection) as session:
            run = await seed_run(session)
            await add_reference(session, run, batch.targets[0].observation.object_key)
            ready.set()
            await release.wait()

    task = asyncio.create_task(writer())
    try:
        await ready.wait()
        assert not (await execute(workflow)).complete
    finally:
        release.set()
        await task
    assert not (await execute(workflow)).complete
    assert (root / batch.targets[0].observation.object_key).exists()


async def test_survey_grace_and_exact_boundary(workflow):
    _, engines, _, _, batch, now, *_ = workflow
    engine = engines["executor"]
    created = max(t.observation.created_at for t in batch.targets)
    report = await survey_workspace(engine, batch, now=created)
    assert not report.complete and all(i.reason == "GRACE_PERIOD" for i in report.items)
    # Earliest receipt is still exactly on its grace boundary, never older.
    boundary = min(t.observation.created_at for t in batch.targets) + timedelta(days=30)
    report = await survey_workspace(engine, batch, now=boundary)
    assert not report.complete and all(i.reason == "GRACE_PERIOD" for i in report.items)
    report = await survey_workspace(engine, batch, now=now)
    assert report.complete and all(i.reason == "REQUIRES_BATCH_REVIEW" for i in report.items)


async def test_partial_failure_retries_only_surviving_target(workflow, monkeypatch):
    from ai_worker.adapters.postgresql_source_cleanup import _LocalSession

    _, engines, _, root, batch, *_ = workflow
    await approve(workflow)
    original = _LocalSession.delete

    async def fail_one(self, target):
        if target == batch.targets[0]:
            raise OSError("synthetic delete failure")
        await original(self, target)

    monkeypatch.setattr(_LocalSession, "delete", fail_one)
    result = await execute(workflow)
    assert not result.complete
    assert (root / batch.targets[0].observation.object_key).exists()
    assert not (root / batch.targets[1].observation.object_key).exists()
    monkeypatch.setattr(_LocalSession, "delete", original)
    result = await execute(workflow, retry=True, max_attempts=2)
    assert result.complete and result.items[1].reason == "ALREADY_RECORDED"
    async with engines["executor"].connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM source_cleanup.audit WHERE batch_hash=:hash"), {"hash": batch.digest()}
            )
            == 6
        )


async def test_separate_cli_process_reloads_and_executes(workflow):
    _, engines, roles, _, batch, *_ = workflow
    await approve(workflow)
    environment = {
        **os.environ,
        "PYTHONPATH": ".",
        "SOURCE_CLEANUP_TEST_DATABASE_URL": engines["executor"].url.render_as_string(hide_password=False),
    }
    arguments = [
        sys.executable,
        "tools/source_cleanup/run.py",
        "execute",
        "--workspace",
        batch.scope.database_id,
        "--batch-hash",
        batch.digest(),
        "--executor",
        roles["executor"],
        "--pm-role",
        roles["pm"],
        "--db-security-role",
        roles["security"],
        "--simulate-elapsed-days",
        "31",
    ]
    for reason in ("DELETE_CONFIRMED", "ALREADY_RECORDED"):
        process = await asyncio.create_subprocess_exec(
            *arguments, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        output, error = await process.communicate()
        assert process.returncode == 0, error.decode()
        result = json.loads(output)
        assert result["complete"]
        assert all(item["reason"] == reason for item in result["items"])


async def test_publisher_rollback_does_not_commit_a_reference(workflow):
    admin, _, _, root, batch, *_ = workflow
    await approve(workflow)
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        async with reference_existing_objects(admin, batch) as connection, AsyncSession(bind=connection) as session:
            run = await seed_run(session)
            await add_reference(session, run, batch.targets[0].observation.object_key)
            raise RuntimeError("synthetic rollback")
    assert (await execute(workflow)).complete


async def test_reference_after_cleanup_cannot_reuse_deleted_receipt(workflow):
    admin, _, _, _, batch, *_ = workflow
    await approve(workflow)
    assert (await execute(workflow)).complete
    with pytest.raises(ValueError, match="Publication object unavailable"):
        async with reference_existing_objects(admin, batch):
            pytest.fail("Deleted object could be reused")


async def test_privileged_account_is_not_execution_role(workflow):
    admin, _, _, _, batch, *_ = workflow
    guard = PostgresLocalCleanupGuard(admin, PostgresAuditJournal(admin))
    with pytest.raises(ValueError, match="restricted execution role"):
        async with guard.acquire(batch):
            pytest.fail("Administrator used as executor")
