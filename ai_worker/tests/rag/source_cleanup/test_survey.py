import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.local_source_cleanup_reader import LocalSourceCleanupReader
from ai_worker.adapters.sqlalchemy_source_cleanup_reader import SqlAlchemySourceCleanupReader
from ai_worker.tasks.rag.source_cleanup.survey import (
    Inventory,
    ObjectObservation,
    ReferenceObservation,
    SurveyDecision,
    SurveyScope,
    survey_candidates,
)

NOW = datetime(2026, 9, 8, tzinfo=UTC)
SCOPE = SurveyScope("synthetic-db", "synthetic-namespace", "LOCAL_PRIVATE", "source-artifact-retention-v1")
OBJ = ObjectObservation("synthetic-key", "a" * 64, 1, NOW - timedelta(days=31), True)
REFS = ReferenceObservation("synthetic-db", 0, "synthetic-namespace", True, 0, True)


async def run(*, obj=OBJ, refs=REFS, scope=SCOPE, complete=True, now=NOW):
    inventory = MagicMock()
    inventory.read_inventory.return_value = Inventory(scope.namespace, (obj,), complete)
    reader = AsyncMock()
    reader.inspect_references.return_value = refs
    return await survey_candidates(scope=scope, now=now, inventory=inventory, references=reader)


async def test_complete_synthetic_evidence_produces_review_only_candidate():
    result = await run()
    assert result.complete
    assert result.items[0].decision is SurveyDecision.REVIEW_CANDIDATE
    assert result.items[0].reason == "REQUIRES_BATCH_REVIEW"
    assert OBJ.object_key not in repr(result)


@pytest.mark.parametrize("count", [1, 2, 9])
async def test_all_shared_direct_references_protect_even_without_snapshot_or_scope(count):
    result = await run(refs=ReferenceObservation("synthetic-db", count))
    assert result.items[0].decision is SurveyDecision.PROTECTED


@pytest.mark.parametrize(
    "change",
    [
        {"direct_count": None},
        {"direct_count": -1},
        {"direct_count": False},
        {"database_id": "other-db"},
        {"namespace": "other-root"},
        {"scope_complete": False},
        {"downstream_count": None},
        {"acquisition_idle": None},
        {"acquisition_idle": False},
    ],
)
async def test_incomplete_or_mismatched_evidence_is_held(change):
    result = await run(refs=replace(REFS, **change))
    assert result.items[0].decision is SurveyDecision.HOLD
    assert not result.complete


async def test_downstream_reference_protects():
    assert (await run(refs=replace(REFS, downstream_count=1))).items[0].decision is SurveyDecision.PROTECTED


@pytest.mark.parametrize(
    "created",
    [None, NOW + timedelta(seconds=1), NOW.replace(tzinfo=None), NOW - timedelta(days=29), NOW - timedelta(days=30)],
)
async def test_unknown_future_or_unexpired_creation_is_held(created):
    assert (await run(obj=replace(OBJ, created_at=created))).items[0].decision is SurveyDecision.HOLD


async def test_boundary_after_30_days_and_ownership():
    assert (await run(obj=replace(OBJ, created_at=NOW - timedelta(days=30, microseconds=1)))).items[
        0
    ].decision is SurveyDecision.REVIEW_CANDIDATE
    assert (await run(obj=replace(OBJ, source_owned=False))).items[0].decision is SurveyDecision.HOLD


@pytest.mark.parametrize(
    "scope,now",
    [
        (replace(SCOPE, policy_version=""), NOW),
        (replace(SCOPE, storage_backend="S3_PRIVATE"), NOW),
        (SCOPE, NOW.replace(tzinfo=None)),
    ],
)
async def test_invalid_scope_never_reads(scope, now):
    inventory = MagicMock()
    refs = AsyncMock()
    result = await survey_candidates(scope=scope, now=now, inventory=inventory, references=refs)
    assert not result.complete and not result.items
    inventory.read_inventory.assert_not_called()
    refs.inspect_references.assert_not_called()


async def test_partial_inventory_and_query_failure_never_leak_details():
    assert not (await run(complete=False)).items
    inventory = MagicMock()
    inventory.read_inventory.return_value = Inventory(SCOPE.namespace, (OBJ,), True)
    refs = AsyncMock()
    refs.inspect_references.side_effect = RuntimeError("synthetic-secret-path-and-password")
    result = await survey_candidates(scope=SCOPE, now=NOW, inventory=inventory, references=refs)
    assert result.items[0].reason == "REFERENCE_UNAVAILABLE"
    assert "synthetic-secret" not in repr(result)
    inventory.read_inventory.side_effect = OSError("synthetic-private-root")
    result = await survey_candidates(scope=SCOPE, now=NOW, inventory=inventory, references=refs)
    assert result.reason == "INVENTORY_UNAVAILABLE"
    assert "synthetic-private" not in repr(result)


async def test_sql_reader_counts_all_rows_without_status_join_or_mutation():
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = 2
    reader = SqlAlchemySourceCleanupReader(session, database_id="synthetic-db")
    result = await reader.inspect_references(storage_backend="LOCAL_PRIVATE", object_key="synthetic-key")
    assert result.direct_count == 2 and not result.scope_complete
    query = session.scalar.await_args.args[0]
    assert set(query.compile().params.values()) == {"LOCAL_PRIVATE", "synthetic-key"}
    sql = str(query)
    assert "count(*)" in sql and "JOIN" not in sql and "run_status" not in sql
    session.commit.assert_not_called()
    session.execute.assert_not_called()


def object_file(tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    payload = b"synthetic-source-only"
    checksum = hashlib.sha256(payload).hexdigest()
    path = root / "sha256" / checksum[:2] / f"{checksum}.artifact"
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    return root, path


def test_local_reads_bytes_without_inventing_creation_or_source_ownership(tmp_path):
    root, path = object_file(tmp_path)
    before = path.read_bytes()
    result = LocalSourceCleanupReader(root).read_inventory()
    assert result.complete and len(result.objects) == 1
    obj = result.objects[0]
    assert obj.checksum == hashlib.sha256(before).hexdigest()
    assert obj.byte_size == len(before)
    assert obj.created_at is None and not obj.source_owned
    assert path.read_bytes() == before


@pytest.mark.parametrize("extra", ["pending", "symlink", "foreign", "corrupt"])
def test_local_incomplete_or_unsafe_inventory_is_not_candidate_input(tmp_path, extra):
    root, path = object_file(tmp_path)
    if extra == "pending":
        (path.parent / ".pending-test").write_text("synthetic")
    elif extra == "symlink":
        (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    elif extra == "foreign":
        (root / "ocr.txt").write_text("synthetic")
    else:
        path.write_text("changed")
    assert not LocalSourceCleanupReader(root).read_inventory().complete


def test_root_symlink_or_broad_permissions_rejected(tmp_path):
    root, path = object_file(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError):
        LocalSourceCleanupReader(link).read_inventory()
    os.chmod(root, 0o755)
    with pytest.raises(ValueError):
        LocalSourceCleanupReader(root).read_inventory()


@pytest.mark.parametrize("changes", [{"checksum": "bad"}, {"byte_size": -1}, {"object_key": " "}])
async def test_invalid_observation_is_not_a_candidate(changes):
    result = await run(obj=replace(OBJ, **changes))
    assert not result.items and result.reason == "OBJECT_METADATA_INVALID"


async def test_listing_namespace_duplicates_and_order():
    inventory = MagicMock()
    references = AsyncMock()
    references.inspect_references.return_value = REFS
    inventory.read_inventory.return_value = Inventory("different-root", (OBJ,), True)
    result = await survey_candidates(scope=SCOPE, now=NOW, inventory=inventory, references=references)
    assert result.reason == "INVENTORY_INCOMPLETE"
    references.inspect_references.assert_not_called()
    inventory.read_inventory.return_value = Inventory(SCOPE.namespace, (OBJ, OBJ), True)
    result = await survey_candidates(scope=SCOPE, now=NOW, inventory=inventory, references=references)
    assert result.reason == "DUPLICATE_OBJECT"
    other = replace(OBJ, object_key="another-object")
    inventory.read_inventory.return_value = Inventory(SCOPE.namespace, (OBJ, other), True)
    forward = await survey_candidates(scope=SCOPE, now=NOW, inventory=inventory, references=references)
    inventory.read_inventory.return_value = Inventory(SCOPE.namespace, (other, OBJ), True)
    reverse = await survey_candidates(scope=SCOPE, now=NOW, inventory=inventory, references=references)
    assert forward == reverse


async def test_real_local_and_sql_readers_cannot_assert_operational_eligibility(tmp_path):
    root, path = object_file(tmp_path)
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = 0
    result = await survey_candidates(
        scope=replace(SCOPE, namespace=str(root)),
        now=NOW,
        inventory=LocalSourceCleanupReader(root),
        references=SqlAlchemySourceCleanupReader(session, database_id=SCOPE.database_id),
    )
    assert result.items[0].decision is SurveyDecision.HOLD
    assert path.exists()
