"""Read-only cleanup evidence on a migrated disposable PostgreSQL database.

Set SOURCE_CLEANUP_TEST_DATABASE_URL explicitly; never uses the application's default DB.
"""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_worker.adapters.sqlalchemy_source_cleanup_reader import SqlAlchemySourceCleanupReader
from ai_worker.tasks.rag.source_cleanup.survey import (
    SurveyDecision,
    classify,
    survey_candidates,
)
from ai_worker.tests.rag.source_cleanup.test_survey import NOW, OBJ, SCOPE
from app.models.rag_source import (
    RagIngestionRunStatus,
    RagSourceIngestionArtifact,
    RagSourceIngestionArtifactKind,
    RagSourceIngestionRun,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)

DATABASE_URL = os.environ.get("SOURCE_CLEANUP_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DATABASE_URL, reason="Explicit disposable PostgreSQL required"),
]


@pytest_asyncio.fixture
async def engine():
    assert DATABASE_URL is not None
    url = make_url(DATABASE_URL)
    if url.host not in {"127.0.0.1", "localhost"} or not (url.database or "").endswith("_cleanup347_test"):
        pytest.fail("Only a named Local disposable cleanup347_test database is allowed")
    db = create_async_engine(url, poolclass=NullPool)
    try:
        async with db.connect() as connection:
            assert await connection.scalar(text("SELECT to_regclass('rag_source_ingestion_artifact')")) is not None
        yield db
    finally:
        await db.dispose()


async def seed_run(session, status=RagIngestionRunStatus.FAILED):
    repo = RagSourceCatalogRepository(session)
    suffix = uuid4().hex
    source = await repo.create_source(RagSourceCreate(source_code="SYNTHETIC_" + suffix, display_name="Synthetic"))
    endpoint = await repo.create_endpoint(RagSourceEndpointCreate(source.id, "LIST", "Synthetic"))
    operation = await repo.create_operation(RagSourceOperationCreate(endpoint.id, "LIST", "Synthetic"))
    snapshot_id = None
    if status != RagIngestionRunStatus.FAILED:
        snapshot = await repo.create_snapshot(
            RagSourceSnapshotCreate(
                operation_id=operation.id,
                source_version=suffix,
                raw_manifest_checksum="a" * 64,
                canonical_checksum="b" * 64,
                schema_version="synthetic-v1",
                parser_version="synthetic-v1",
                normalization_version="synthetic-v1",
                canonicalization_spec_version="synthetic-v1",
                record_count=1,
                rejected_record_count=0,
                collected_at=datetime.now(UTC),
            )
        )
        snapshot_id = snapshot.id
    run = RagSourceIngestionRun(
        operation_id=operation.id,
        run_group_key=suffix,
        snapshot_id=snapshot_id,
        run_status=status,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run


async def add_reference(session, run, key, kind=RagSourceIngestionArtifactKind.RAW_RESPONSE):
    artifact = RagSourceIngestionArtifact(
        ingestion_run_id=run.id,
        artifact_kind=kind,
        artifact_key="synthetic-" + uuid4().hex,
        storage_backend="LOCAL_PRIVATE",
        object_key=key,
        raw_checksum="c" * 64,
        byte_size=1,
        content_type="application/json",
        page_number=1 if kind == RagSourceIngestionArtifactKind.RAW_RESPONSE else None,
        reject_code="SYNTHETIC_REJECT" if kind == RagSourceIngestionArtifactKind.REJECTS else None,
        parser_location="synthetic-row" if kind == RagSourceIngestionArtifactKind.REJECTS else None,
    )
    session.add(artifact)
    await session.flush()
    return artifact


@pytest.mark.parametrize("status", [RagIngestionRunStatus.FAILED, RagIngestionRunStatus.NO_CHANGE])
@pytest.mark.parametrize("kind", list(RagSourceIngestionArtifactKind))
async def test_failed_and_no_change_references_protect_objects(engine, status, kind):
    async with AsyncSession(engine) as session:
        run = await seed_run(session, status)
        key = uuid4().hex
        await add_reference(session, run, key, kind)
        reader = SqlAlchemySourceCleanupReader(session, database_id=SCOPE.database_id)
        refs = await reader.inspect_references(storage_backend="LOCAL_PRIVATE", object_key=key)
        assert refs.direct_count == 1
        assert classify(OBJ, refs, SCOPE, NOW)[0] == SurveyDecision.PROTECTED
        assert (run.snapshot_id is None) == (status == RagIngestionRunStatus.FAILED)
        # The reader did not commit seed data; closing the session rolls it back.
        assert session.in_transaction()


async def test_shared_object_counts_all_runs_and_kinds(engine):
    async with AsyncSession(engine) as session:
        key = uuid4().hex
        for kind in RagSourceIngestionArtifactKind:
            run = await seed_run(session)
            await add_reference(session, run, key, kind)
        refs = await SqlAlchemySourceCleanupReader(session, database_id=SCOPE.database_id).inspect_references(
            storage_backend="LOCAL_PRIVATE",
            object_key=key,
        )
        assert refs.direct_count == 2
        assert classify(OBJ, refs, SCOPE, NOW)[0] == SurveyDecision.PROTECTED


async def test_zero_direct_count_is_not_complete_reference_proof(engine):
    async with AsyncSession(engine) as session:
        refs = await SqlAlchemySourceCleanupReader(session, database_id=SCOPE.database_id).inspect_references(
            storage_backend="LOCAL_PRIVATE",
            object_key=uuid4().hex,
        )
        assert refs.direct_count == 0
        assert not refs.scope_complete and refs.namespace is None
        assert classify(OBJ, refs, SCOPE, NOW)[0] == SurveyDecision.HOLD


async def test_uncommitted_reference_visibility_never_grants_eligibility(engine):
    # Real independent transactions demonstrate why SELECT count=0 is insufficient for deletion.
    key = uuid4().hex
    async with AsyncSession(engine) as writer, AsyncSession(engine) as observer:
        run = await seed_run(writer)
        await add_reference(writer, run, key)
        reader = SqlAlchemySourceCleanupReader(observer, database_id=SCOPE.database_id)
        before = await reader.inspect_references(storage_backend="LOCAL_PRIVATE", object_key=key)
        assert before.direct_count == 0
        assert classify(OBJ, before, SCOPE, NOW)[0] == SurveyDecision.HOLD
        await writer.commit()
        after = await reader.inspect_references(storage_backend="LOCAL_PRIVATE", object_key=key)
        assert after.direct_count == 1
        assert classify(OBJ, after, SCOPE, NOW)[0] == SurveyDecision.PROTECTED
    # This committed synthetic row is retained until the disposable test container is removed.


async def test_sql_error_is_reported_as_hold(engine):
    from unittest.mock import Mock

    from ai_worker.tasks.rag.source_cleanup.survey import Inventory

    async with AsyncSession(engine) as session:
        await session.execute(text("SET LOCAL search_path TO pg_catalog"))
        inventory = Mock()
        inventory.read_inventory.return_value = Inventory(SCOPE.namespace, (OBJ,), True)
        result = await survey_candidates(
            scope=SCOPE,
            now=NOW,
            inventory=inventory,
            references=SqlAlchemySourceCleanupReader(session, database_id=SCOPE.database_id),
        )
        assert not result.complete
        assert result.items[0].reason == "REFERENCE_UNAVAILABLE"
