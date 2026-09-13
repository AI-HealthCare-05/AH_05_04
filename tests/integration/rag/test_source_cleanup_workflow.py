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
    record_review,
    reference_existing_objects,
    require_synthetic_database,
    revoke_review_batch,
    survey_workspace,
)
from ai_worker.tasks.rag.source_cleanup.execution import AuditEntry, execute_synthetic_batch
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
        assert (
            await connection.scalar(
                text("""SELECT count(*) FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='source_cleanup' AND NOT t.tgisinternal""")
            )
            == 0
        )
        assert (
            await connection.scalar(
                text("""SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='source_cleanup'""")
            )
            == 0
        )
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
            await connection.execute(text(f"GRANT USAGE ON SEQUENCE source_cleanup.review_revision TO {role}"))
        executor = roles["executor"]
        await connection.execute(
            text(f"""GRANT INSERT (batch_hash,object_ref,attempt_id,event,payload,intent_sequence,recorded_at)
            ON source_cleanup.audit TO {executor}""")
        )
        await connection.execute(text(f"GRANT USAGE ON SEQUENCE source_cleanup.audit_sequence_seq TO {executor}"))
        for key, role in (("security", "DB_SECURITY"), ("pm", "PM")):
            await connection.execute(
                text("INSERT INTO source_cleanup.reviewer(actor,role) VALUES (:actor,:role)"),
                {"actor": roles[key], "role": role},
            )
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
    workspace = await create_synthetic_workspace(admin, root, evaluation_offset_days=31)
    batch = await load_batch(engines["executor"], workspace)
    # Simulate elapsed time, without backdating physical metadata or the DB publication receipt.
    now = max(t.observation.created_at for t in batch.targets) + timedelta(days=31)
    verifier = PostgresApprovalVerifier(engines["executor"], pm_role=roles["pm"], db_security_role=roles["security"])
    journal = PostgresAuditJournal(engines["executor"])
    guard = PostgresLocalCleanupGuard(engines["executor"], journal)
    return admin, engines, roles, root, batch, now, verifier, journal, guard


async def approve(workflow, *, expires=None, executor=None, order=("security", "pm")):
    _, engines, roles, _, batch, now, *_ = workflow
    for key in order:
        role = {"security": "DB_SECURITY", "pm": "PM"}[key]
        await record_review(
            engines[key],
            batch_hash=batch.digest(),
            role=role,
            executor=executor or roles["executor"],
            policy_version=batch.scope.policy_version,
            valid_from=now - timedelta(hours=1),
            expires_at=expires or now + timedelta(hours=1),
        )


async def revoke(workflow):
    _, engines, _, _, batch, *_ = workflow
    await revoke_review_batch(engines["pm"], batch_hash=batch.digest())


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


async def test_committed_direct_reference_blocks_cleanup(workflow):
    admin, _, _, root, batch, *_ = workflow
    await approve(workflow)
    async with AsyncSession(admin) as writer:
        run = await seed_run(writer)
        await add_reference(writer, run, batch.targets[0].observation.object_key)
        await writer.commit()
    assert not (await execute(workflow)).complete
    assert all((root / t.observation.object_key).exists() for t in batch.targets)


@pytest.mark.parametrize("change", ["revoke", "expire", "replace", "schema", "wrong_scope"])
async def test_changed_evidence_blocks(workflow, change):
    admin, engines, _, root, batch, now, *_ = workflow
    await approve(workflow, expires=now if change == "expire" else None)
    if change == "revoke":
        await revoke(workflow)
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
    # The schema owner is the explicit migration/recovery boundary, never an app credential.
    assert admin.url.username != engines["executor"].url.username
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


@pytest.mark.parametrize("change", ["expire", "executor"])
async def test_append_only_reapproval_corrects_latest_review(workflow, change):
    _, engines, roles, _, batch, now, verifier, *_ = workflow
    await approve(
        workflow,
        expires=now if change == "expire" else None,
        executor="wrong_executor" if change == "executor" else None,
    )
    assert not (await execute(workflow)).complete
    await approve(workflow)
    receipt = await verifier.verify(batch_digest=batch.digest(), executor=roles["executor"])
    assert receipt is not None
    assert (await execute(workflow)).complete
    async with engines["executor"].connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT revision FROM source_cleanup.review WHERE batch_hash=:hash ORDER BY revision"),
                {"hash": batch.digest()},
            )
        ).all()
        assert len(rows) == 4 and len(set(rows)) == 4
        receipts = (
            await connection.scalars(
                text("SELECT payload->>'receipt_id' FROM source_cleanup.audit WHERE batch_hash=:hash"),
                {"hash": batch.digest()},
            )
        ).all()
        assert set(receipts) == {receipt.receipt_id}


async def test_invalid_latest_review_never_falls_back_to_previous(workflow):
    await approve(workflow)
    await approve(workflow, executor="wrong_executor")
    assert not (await execute(workflow)).complete


async def test_revocation_is_terminal_and_history_is_preserved(workflow):
    await approve(workflow)
    await revoke(workflow)
    with pytest.raises(ValueError, match="revoked"):
        await approve(workflow)
    assert not (await execute(workflow)).complete


async def test_revoke_committed_after_precheck_prevents_every_unlink(workflow, monkeypatch):
    _, engines, _, root, batch, _, verifier, *_ = workflow
    await approve(workflow)
    original = verifier.verify
    ready, revoked = asyncio.Event(), asyncio.Event()

    async def verify_then_pause(**kwargs):
        receipt = await original(**kwargs)
        ready.set()
        await asyncio.wait_for(revoked.wait(), 5)
        return receipt

    async def commit_revoke():
        await asyncio.wait_for(ready.wait(), 5)
        await revoke_review_batch(engines["pm"], batch_hash=batch.digest())
        revoked.set()

    monkeypatch.setattr(verifier, "verify", verify_then_pause)
    result, _ = await asyncio.gather(execute(workflow), commit_revoke())
    assert not result.complete
    assert all((root / t.observation.object_key).exists() for t in batch.targets)


@pytest.mark.parametrize("change", ["revoke", "review"])
async def test_review_write_cannot_commit_between_final_verify_and_unlink(workflow, monkeypatch, change):
    from ai_worker.adapters.postgresql_source_cleanup import _LocalSession

    _, engines, _, root, batch, *_ = workflow
    await approve(workflow)
    original = _LocalSession.delete
    reached, attempted = asyncio.Event(), asyncio.Event()

    async def pause_before_unlink(self, target):
        reached.set()
        await asyncio.wait_for(attempted.wait(), 5)
        await original(self, target)

    async def change_review():
        await asyncio.wait_for(reached.wait(), 5)
        with pytest.raises(ValueError, match="review busy"):
            if change == "review":
                await approve(workflow)
            else:
                await revoke_review_batch(engines["pm"], batch_hash=batch.digest())
        attempted.set()

    monkeypatch.setattr(_LocalSession, "delete", pause_before_unlink)
    result, _ = await asyncio.gather(execute(workflow), change_review())
    assert result.complete
    assert all(not (root / t.observation.object_key).exists() for t in batch.targets)
    # The failed write was not reported as committed; an explicit retry can now commit.
    if change == "review":
        await approve(workflow)
    else:
        await revoke(workflow)


async def test_inflight_revocation_blocks_execution(workflow):
    _, engines, _, root, batch, *_ = workflow
    await approve(workflow)
    async with engines["pm"].begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(347, hashtext(:hash))"), {"hash": batch.digest()})
        await connection.execute(
            text("INSERT INTO source_cleanup.revocation(batch_hash) VALUES (:hash)"), {"hash": batch.digest()}
        )
        assert not (await execute(workflow)).complete
    assert all((root / t.observation.object_key).exists() for t in batch.targets)


@pytest.mark.parametrize("change", ["database", "environment", "namespace", "ownership"])
async def test_downstream_scope_requires_proven_synthetic_origin(workflow, monkeypatch, change):
    import ai_worker.adapters.postgresql_source_cleanup as adapter

    *_, batch, now, verifier, journal, guard = workflow
    async with guard.acquire(batch) as session:
        original = session.batch
        if change == "database":

            async def non_synthetic(connection):
                raise ValueError("Not a synthetic database")

            monkeypatch.setattr(adapter, "require_synthetic_database", non_synthetic)
        elif change == "environment":
            session.batch = replace(batch, environment="PRODUCTION")
        elif change == "namespace":
            session.batch = replace(batch, scope=replace(batch.scope, namespace="/unproved"))
        else:
            target = batch.targets[0]
            session.batch = replace(
                batch,
                targets=(
                    replace(target, observation=replace(target.observation, source_owned=False)),
                    *batch.targets[1:],
                ),
            )
        try:
            with pytest.raises(NotImplementedError, match="downstream reference survey unavailable"):
                await session._inspect_downstream_scope(batch.targets[0].observation.object_key)
        finally:
            session.batch = original


@pytest.mark.parametrize("evidence", ["downstream", "incomplete", "unimplemented"])
async def test_adapter_downstream_evidence_reaches_fail_closed_survey(workflow, monkeypatch, evidence):
    from ai_worker.adapters.postgresql_source_cleanup import _LocalSession

    _, engines, _, root, batch, now, *_ = workflow
    await approve(workflow)
    original = _LocalSession._inspect_downstream_scope

    async def inspect(self, key):
        if evidence == "unimplemented":
            raise NotImplementedError("Operational scope unavailable")
        observed = await original(self, key)
        return (
            replace(observed, downstream_count=1)
            if evidence == "downstream"
            else replace(observed, scope_complete=False)
        )

    monkeypatch.setattr(_LocalSession, "_inspect_downstream_scope", inspect)
    report = await survey_workspace(engines["executor"], batch, now=now)
    assert report.complete is (evidence == "downstream")
    if evidence != "unimplemented":
        reason = "DOWNSTREAM_REFERENCE" if evidence == "downstream" else "REFERENCE_SCOPE_INCOMPLETE"
        assert all(item.reason == reason for item in report.items)
    assert not (await execute(workflow)).complete
    assert all((root / t.observation.object_key).exists() for t in batch.targets)


async def append_report(engine, batch, *, event="INTENT", reason="FINAL_CHECKS_PASSED", attempt=None, ref=None):
    import hashlib

    target = batch.targets[0]
    object_ref = ref or hashlib.sha256(f"{batch.digest()}:{target.observation.object_key}".encode()).hexdigest()
    attempt = attempt or str(uuid4())
    await PostgresAuditJournal(engine).append(
        AuditEntry(
            batch.digest(),
            object_ref,
            attempt,
            event,
            reason,
            target.observation.checksum,
            target.artifact_kind,
            batch.scope.policy_version,
            "ignored_receipt",
            "ignored_pm",
            "ignored_security",
            engine.url.username or "missing_executor",
            "1900-01-01T00:00:00+00:00",
        )
    )
    return attempt


async def test_security_review_must_precede_final_pm_approval(workflow):
    _, engines, _, root, batch, *_ = workflow
    await approve(workflow, order=("pm", "security"))
    assert not (await execute(workflow)).complete
    with pytest.raises(ValueError, match="Audit approval invalid"):
        await append_report(engines["executor"], batch)
    assert all((root / t.observation.object_key).exists() for t in batch.targets)
    await approve(workflow, order=("pm",))
    assert (await execute(workflow)).complete


async def test_new_security_revision_requires_pm_review_again(workflow):
    _, engines, _, root, batch, *_ = workflow
    await approve(workflow)
    await approve(workflow, order=("security",))
    assert not (await execute(workflow)).complete
    assert all((root / t.observation.object_key).exists() for t in batch.targets)
    await approve(workflow, order=("pm",))
    assert (await execute(workflow)).complete


@pytest.mark.parametrize("change", ["expired", "wrong-executor", "revoked"])
async def test_audit_journal_cannot_bypass_current_approval(workflow, change):
    _, engines, _, _, batch, now, *_ = workflow
    await approve(
        workflow,
        expires=now if change == "expired" else None,
        executor="wrong_executor" if change == "wrong-executor" else None,
    )
    if change == "revoked":
        await revoke(workflow)
    with pytest.raises(ValueError, match="Audit approval invalid"):
        await append_report(engines["executor"], batch)


async def test_executor_has_no_freeform_audit_or_trusted_receipt_write(workflow):
    _, engines, _, _, batch, *_ = workflow
    await approve(workflow)
    async with engines["executor"].begin() as connection:
        with pytest.raises(DBAPIError, match="check constraint"):
            await connection.execute(
                text("""INSERT INTO source_cleanup.audit (batch_hash,object_ref,attempt_id,event,payload)
                VALUES ('x','y','z','INTENT','{}')""")
            )
    for table, columns, values in (
        ("batch_target", "batch_hash,object_ref,workspace_id,object_key", "'x','y','z','k'"),
        ("reviewer", "actor,role", "'forged','PM'"),
    ):
        async with engines["executor"].begin() as connection:
            with pytest.raises(DBAPIError, match="permission denied"):
                await connection.execute(text(f"INSERT INTO source_cleanup.{table} ({columns}) VALUES ({values})"))


async def test_audit_evidence_is_derived_from_database_not_entry_fields(workflow):
    from ai_worker.tasks.rag.source_cleanup.execution import AuditEntry, _object_ref

    _, engines, roles, _, batch, now, verifier, journal, _ = workflow
    await approve(workflow)
    receipt = await verifier.verify(batch_digest=batch.digest(), executor=roles["executor"])
    attempt = str(uuid4())
    target = batch.targets[0]
    entry = AuditEntry(
        batch.digest(),
        _object_ref(batch, target),
        attempt,
        "INTENT",
        "FINAL_CHECKS_PASSED",
        "0" * 64,
        "REJECTS",
        batch.scope.policy_version,
        "forged_receipt",
        "forged_pm",
        "forged_security",
        roles["executor"],
        "1900-01-01T00:00:00+00:00",
        False,
    )
    await journal.append(entry)
    await journal.append(replace(entry, event="UNKNOWN", reason="DELETE_RESULT_UNKNOWN"))
    history = await journal.history(batch.digest(), entry.object_ref)
    assert len(history) == 2
    for recorded in history:
        assert recorded.receipt_id == receipt.receipt_id
        assert recorded.pm_actor == roles["pm"] and recorded.db_security_actor == roles["security"]
        assert recorded.checksum == target.observation.checksum and recorded.artifact_kind == target.artifact_kind
        assert recorded.references_verified is True
        assert not recorded.occurred_at.startswith("1900")
    async with engines["executor"].connect() as connection:
        assert await connection.scalar(
            text(
                "SELECT bool_and((payload->>'occurred_at')::timestamptz=recorded_at) FROM source_cleanup.audit WHERE attempt_id=:id"
            ),
            {"id": attempt},
        )


@pytest.mark.parametrize("change", ["reason", "target", "no-intent"])
async def test_audit_journal_rejects_unbound_or_freeform_reports(workflow, change):
    _, engines, _, _, batch, *_ = workflow
    await approve(workflow)
    with pytest.raises((DBAPIError, ValueError)):
        await append_report(
            engines["executor"],
            batch,
            ref="0" * 64 if change == "target" else None,
            event="DELETED" if change == "no-intent" else "INTENT",
            reason="DELETE_CONFIRMED"
            if change == "no-intent"
            else "private injected reason"
            if change == "reason"
            else "FINAL_CHECKS_PASSED",
        )


async def test_direct_audit_intent_preserves_referenced_objects(workflow):
    admin, engines, _, _, batch, *_ = workflow
    await approve(workflow)
    async with publication_transaction(admin) as connection, AsyncSession(bind=connection) as session:
        run = await seed_run(session)
        await add_reference(session, run, batch.targets[0].observation.object_key)
    with pytest.raises(ValueError, match="Audit reference invalid"):
        await append_report(engines["executor"], batch)


async def test_cli_clock_cannot_differ_from_registered_workspace(workflow):
    from argparse import Namespace

    from tools.source_cleanup.run import require_workspace_clock

    _, engines, _, _, batch, *_ = workflow
    args = Namespace(command="execute", workspace=batch.scope.database_id, simulate_elapsed_days=None)
    with pytest.raises(ValueError, match="clock differs"):
        await require_workspace_clock(engines["executor"], args)
    args.simulate_elapsed_days = 31
    await require_workspace_clock(engines["executor"], args)


async def test_caller_future_clock_cannot_bypass_database_time(workflow, tmp_path):
    admin, engines, _, _, _, now, *_ = workflow
    workspace = await create_synthetic_workspace(admin, tmp_path.resolve() / "real-clock")
    batch = await load_batch(engines["executor"], workspace)
    changed = (*workflow[:4], batch, *workflow[5:])
    # Caller-side simulated future approvals do not advance a workspace registered at offset zero.
    await approve(changed)
    with pytest.raises(ValueError, match="Audit approval invalid"):
        await append_report(engines["executor"], batch)
