from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_worker.adapters.synthetic_source_cleanup import SyntheticCleanupLab, SyntheticJournal, _SyntheticSession
from ai_worker.tasks.rag.source_cleanup.execution import execute_synthetic_batch

NOW = datetime(2026, 9, 8, tzinfo=UTC)


@pytest.fixture
def lab():
    fixture = SyntheticCleanupLab(now=NOW)
    try:
        yield fixture
    finally:
        fixture.close()


async def execute(lab, **kwargs):
    return await execute_synthetic_batch(
        batch=kwargs.pop("batch", lab.batch),
        executor="synthetic-executor",
        clock=lambda: NOW,
        approvals=lab,
        guard=lab,
        **kwargs,
    )


def path(lab, target):
    return Path(lab._directory.name) / target.observation.object_key


def records(lab):
    # Fresh reader on every call: outcome recovery does not rely on a cached in-memory journal.
    return SyntheticJournal(lab._fd)._entries()


async def test_real_unlink_durable_intent_and_idempotent_second_call(lab, monkeypatch):
    original = _SyntheticSession.delete

    async def checked_delete(session, target):
        assert session.held
        assert records(lab)[-1].event == "INTENT"
        await original(session, target)

    monkeypatch.setattr(_SyntheticSession, "delete", checked_delete)
    result = await execute(lab)
    assert result.complete
    assert all(not path(lab, target).exists() for target in lab.batch.targets)
    history = records(lab)
    assert [entry.event for entry in history] == ["INTENT", "DELETED", "INTENT", "DELETED"]
    assert {entry.artifact_kind for entry in history} == {"RAW_RESPONSE", "REJECTS"}
    second = await execute(lab)
    assert second.complete
    assert all(item.reason == "ALREADY_RECORDED" for item in second.items)
    assert records(lab) == history


@pytest.mark.parametrize("event", ["INTENT", "DELETED"])
async def test_audit_failure_stops_batch_and_missing_recovery_stays_unknown(lab, monkeypatch, event):
    append = SyntheticJournal.append

    def fail(journal, entry):
        if entry.event == event:
            raise OSError("private-provider-secret")
        append(journal, entry)

    monkeypatch.setattr(SyntheticJournal, "append", fail)
    result = await execute(lab)
    assert not result.complete and "private" not in repr(result)
    missing = sum(not path(lab, target).exists() for target in lab.batch.targets)
    assert missing == (1 if event == "DELETED" else 0)
    monkeypatch.setattr(SyntheticJournal, "append", append)
    if event == "DELETED":
        recovered = await execute(lab, retry=True, max_attempts=2)
        assert not recovered.complete
        assert any(item.reason == "MISSING_REQUIRES_RECONCILIATION" for item in recovered.items)
        assert records(lab)[1].event == "UNKNOWN"
        assert records(lab)[0].event == "INTENT"


async def test_partial_failure_retries_only_survivor_with_fresh_checks(lab, monkeypatch):
    target = sorted(lab.batch.targets, key=lambda t: t.observation.object_key)[-1]
    original = _SyntheticSession.delete
    calls = []

    async def fail_one(session, item):
        calls.append(item)
        if item == target and calls.count(item) == 1:
            raise OSError("private-provider-secret")
        await original(session, item)

    monkeypatch.setattr(_SyntheticSession, "delete", fail_one)
    first = await execute(lab)
    assert not first.complete
    assert sorted(i.outcome for i in first.items) == ["DELETED", "UNKNOWN"]
    assert path(lab, target).exists()
    second = await execute(lab, retry=True, max_attempts=2)
    assert second.complete
    assert len(calls) == 3
    assert [e.event for e in records(lab)] == ["INTENT", "DELETED", "INTENT", "UNKNOWN", "INTENT", "DELETED"]


@pytest.mark.parametrize("change", ["reference", "approval", "bytes", "generation"])
async def test_retry_rechecks_reference_approval_and_object(lab, monkeypatch, change):
    original = _SyntheticSession.delete

    async def fail(session, target):
        raise OSError("synthetic-failure")

    monkeypatch.setattr(_SyntheticSession, "delete", fail)
    await execute(lab)
    monkeypatch.setattr(_SyntheticSession, "delete", original)
    if change == "reference":
        lab.reference_count = 1
    elif change == "approval":
        lab.approval = replace(lab.approval, expires_at=NOW)
    else:
        target = lab.batch.targets[0]
        data = path(lab, target).read_bytes()
        path(lab, target).write_bytes(data if change == "generation" else b"changed")
    result = await execute(lab, retry=True, max_attempts=2)
    assert not result.complete
    if change in {"reference", "approval"}:
        assert all(path(lab, t).exists() for t in lab.batch.targets)
    else:
        assert path(lab, lab.batch.targets[0]).exists()


async def test_retry_is_explicit_and_bounded(lab, monkeypatch):
    calls = []

    async def fail(session, target):
        calls.append(target)
        raise OSError("synthetic-failure")

    monkeypatch.setattr(_SyntheticSession, "delete", fail)
    await execute(lab)
    await execute(lab)
    assert len(calls) == 2
    await execute(lab, retry=True, max_attempts=2)
    assert len(calls) == 4
    result = await execute(lab, retry=True, max_attempts=2)
    assert len(calls) == 4 and not result.complete


@pytest.mark.parametrize("change", ["reference", "expiry", "guard", "bytes"])
async def test_change_during_intent_write_prevents_delete(lab, monkeypatch, change):
    append = SyntheticJournal.append

    def mutate(journal, entry):
        append(journal, entry)
        if entry.event != "INTENT":
            return
        if change == "reference":
            lab.reference_count = 1
        elif change == "expiry":
            lab.approval = replace(lab.approval, expires_at=NOW)
        elif change == "bytes":
            for target in lab.batch.targets:
                path(lab, target).write_bytes(b"changed")
        else:

            async def lost(session):
                raise ValueError("lost")

            monkeypatch.setattr(_SyntheticSession, "assert_held", lost)

    monkeypatch.setattr(SyntheticJournal, "append", mutate)
    result = await execute(lab)
    assert not result.complete
    assert all(path(lab, target).exists() for target in lab.batch.targets)
    assert all(entry.event != "DELETED" for entry in records(lab))


async def test_process_interruption_after_unlink_recovers_from_disk(lab, monkeypatch):
    class Interrupted(BaseException):
        pass

    original = _SyntheticSession.delete

    async def interrupted(session, target):
        await original(session, target)
        raise Interrupted()

    monkeypatch.setattr(_SyntheticSession, "delete", interrupted)
    with pytest.raises(Interrupted):
        await execute(lab)
    assert [entry.event for entry in records(lab)] == ["INTENT"]
    monkeypatch.setattr(_SyntheticSession, "delete", original)
    result = await execute(lab, retry=True, max_attempts=2)
    assert not result.complete
    assert records(lab)[1].event == "UNKNOWN"


async def test_corrupt_audit_prevents_delete(lab):
    (Path(lab._directory.name) / "audit.jsonl").write_bytes(b'{"incomplete":')
    result = await execute(lab)
    assert not result.complete
    assert all(path(lab, target).exists() for target in lab.batch.targets)


async def test_recreated_successful_object_is_not_deleted(lab):
    await execute(lab)
    target = lab.batch.targets[0]
    path(lab, target).write_bytes(b"new-generation")
    result = await execute(lab, retry=True, max_attempts=2)
    assert not result.complete
    assert path(lab, target).read_bytes() == b"new-generation"
    assert any(i.reason == "RECREATED_AFTER_SUCCESS" for i in result.items)


async def test_symlink_does_not_delete_external_file(lab, tmp_path):
    external = tmp_path / "external"
    external.write_bytes(b"synthetic-external")
    target = lab.batch.targets[0]
    path(lab, target).unlink()
    path(lab, target).symlink_to(external)
    result = await execute(lab)
    assert not result.complete
    assert external.read_bytes() == b"synthetic-external"
    assert path(lab, target).is_symlink()


async def test_second_executor_cannot_enter_held_lab(lab):
    async with lab.acquire(lab.batch):
        result = await execute(lab)
        assert not result.complete
    assert all(path(lab, target).exists() for target in lab.batch.targets)


@pytest.mark.parametrize("change", ["scope", "path", "kind", "environment"])
async def test_lab_cannot_accept_arbitrary_store_or_target(lab, change):
    batch = lab.batch
    if change == "scope":
        batch = replace(batch, scope=replace(batch.scope, namespace="/real-store"))
    elif change == "environment":
        batch = replace(batch, environment="PRODUCTION")
    else:
        target = batch.targets[0]
        target = (
            replace(target, artifact_kind="OCR")
            if change == "kind"
            else replace(target, observation=replace(target.observation, object_key="../external"))
        )
        batch = replace(batch, targets=(target,))
    result = await execute(lab, batch=batch)
    assert not result.complete
    assert all(path(lab, target).exists() for target in lab.batch.targets)


async def test_audit_is_append_only_api_and_redacts_object_paths(lab):
    await execute(lab)
    journal = SyntheticJournal(lab._fd)
    assert not hasattr(journal, "update") and not hasattr(journal, "delete")
    data = (Path(lab._directory.name) / "audit.jsonl").read_text()
    assert lab._directory.name not in data
    assert all(t.observation.object_key not in data for t in lab.batch.targets)
    assert "SYNTHETIC SOURCE FIXTURE" not in data
    with pytest.raises(ValueError, match="Duplicate"):
        journal.append(records(lab)[0])


async def test_result_append_ack_lost_recovers_recorded_success(lab, monkeypatch):
    original = SyntheticJournal.append
    raised = False

    def uncertain(journal, entry):
        nonlocal raised
        original(journal, entry)
        if entry.event == "DELETED" and not raised:
            raised = True
            raise OSError("synthetic-ack-lost")

    monkeypatch.setattr(SyntheticJournal, "append", uncertain)
    assert not (await execute(lab)).complete
    monkeypatch.setattr(SyntheticJournal, "append", original)
    result = await execute(lab, retry=True, max_attempts=2)
    assert result.complete
    assert sum(e.event == "INTENT" for e in records(lab)) == 2


async def test_partial_intent_write_blocks_recovery_without_deleting(lab, monkeypatch):
    def torn(journal, entry):
        with (Path(lab._directory.name) / "audit.jsonl").open("ab") as stream:
            stream.write(b'{"partial":')
        raise OSError("synthetic-torn-write")

    monkeypatch.setattr(SyntheticJournal, "append", torn)
    assert not (await execute(lab)).complete
    assert not (await execute(lab, retry=True, max_attempts=2)).complete
    assert all(path(lab, target).exists() for target in lab.batch.targets)


async def test_fsync_failure_before_delete_leaves_files(lab, monkeypatch):
    import os

    def unavailable(descriptor):
        raise OSError("synthetic-fsync-failure")

    monkeypatch.setattr(os, "fsync", unavailable)
    result = await execute(lab)
    assert not result.complete
    assert all(path(lab, target).exists() for target in lab.batch.targets)


@pytest.mark.parametrize("limit", [0, 4, True])
async def test_invalid_retry_limit_never_deletes(lab, limit):
    result = await execute(lab, max_attempts=limit)
    assert not result.complete
    assert records(lab) == []
    assert all(path(lab, target).exists() for target in lab.batch.targets)


async def test_new_journal_reader_in_subprocess_sees_intent_and_result(lab):
    import subprocess
    import sys

    assert (await execute(lab)).complete
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os,sys; from ai_worker.adapters.synthetic_source_cleanup import SyntheticJournal; "
                "fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); "
                "print(','.join(e.event for e in SyntheticJournal(fd)._entries())); os.close(fd)"
            ),
            lab._directory.name,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "INTENT,DELETED,INTENT,DELETED"
