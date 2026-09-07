from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_source_snapshot_repository import (
    SqlAlchemySourceSnapshotRepository,
)
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    StoredRawArtifact,
)
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotCreateRequest,
    SnapshotIngestionMetadata,
    SnapshotRunRecord,
    SnapshotVerificationStatus,
)

_OPERATION_ID = UUID("11111111-1111-4111-8111-111111111111")
_SNAPSHOT_ID = UUID("22222222-2222-4222-8222-222222222222")
_NOW = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)


def _identity() -> SourceOperationIdentity:
    return SourceOperationIdentity(
        source_code="MFDS_PRODUCT_APPROVAL",
        endpoint_code="MFDS_PRODUCT_APPROVAL_API",
        operation_code="LIST_APPROVED_PRODUCTS",
    )


def _metadata() -> SnapshotIngestionMetadata:
    return SnapshotIngestionMetadata(
        source_version="source-v2",
        schema_version="schema-v1",
        parser_version="parser-v1",
        normalization_version="normalization-v1",
        rejected_record_count=0,
        run_group_key="synthetic-run",
        attempt_number=1,
        started_at=_NOW,
        finished_at=_NOW,
        collected_at=_NOW,
    )


def _ingestion() -> ProductIngestionResult:
    return ProductIngestionResult(
        identity=_identity(),
        endpoint_receipt_hash="a" * 64,
        raw_manifest_checksum="b" * 64,
        canonical_checksum="c" * 64,
        canonicalization_spec_version="mfds-product-approval@1",
        record_count=2,
        artifact_count=1,
    )


async def test_operation_lookup_locks_exact_source_endpoint_and_operation() -> None:
    session = AsyncMock(spec=AsyncSession)
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = str(_OPERATION_ID)
    session.execute.return_value = query_result
    repository = SqlAlchemySourceSnapshotRepository(session)

    operation_id = await repository.lock_operation(_identity())

    assert operation_id == _OPERATION_ID
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    assert "rag_source_operation JOIN rag_source_endpoint" in sql
    assert "JOIN rag_source" in sql
    assert "rag_source.source_code" in sql
    assert "rag_source_endpoint.endpoint_code" in sql
    assert "rag_source_operation.operation_code" in sql
    assert "FOR UPDATE" in sql


async def test_snapshot_candidate_is_inserted_as_pending_without_commit() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = SqlAlchemySourceSnapshotRepository(session)

    snapshot_id = await repository.create_snapshot(
        SnapshotCreateRequest(
            operation_id=_OPERATION_ID,
            ingestion=_ingestion(),
            metadata=_metadata(),
            supersedes_snapshot_id=_SNAPSHOT_ID,
        )
    )

    assert isinstance(snapshot_id, UUID)
    statement = session.execute.await_args.args[0]
    parameters = statement.compile().params
    assert "INSERT INTO rag_source_snapshot" in str(statement)
    assert parameters["verification_status"] == "PENDING"
    assert parameters["supersedes_snapshot_id"] == str(_SNAPSHOT_ID)
    assert parameters["canonical_checksum"] == "c" * 64
    session.commit.assert_not_awaited()


async def test_conflict_run_records_only_safe_failure_code() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = SqlAlchemySourceSnapshotRepository(session)

    ingestion_run_id = await repository.create_run(
        SnapshotRunRecord(
            operation_id=_OPERATION_ID,
            snapshot_id=None,
            run_group_key="synthetic-run",
            attempt_number=1,
            run_status="FAILED",
            failure_code="SOURCE_VERSION_CONFLICT",
            started_at=_NOW,
            finished_at=_NOW,
            duration_ms=10,
        )
    )

    statement = session.execute.await_args.args[0]
    parameters = statement.compile().params
    assert parameters["failure_code"] == "SOURCE_VERSION_CONFLICT"
    assert parameters["failure_message"] is None
    assert isinstance(ingestion_run_id, UUID)
    session.commit.assert_not_awaited()


async def test_artifact_references_are_inserted_without_raw_content_or_commit() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = SqlAlchemySourceSnapshotRepository(session)
    artifact = StoredRawArtifact(
        page_number=1,
        metadata=RawArtifactMetadata(
            artifact_key="page-0001.json",
            raw_checksum="d" * 64,
            byte_size=128,
            content_type="application/json",
        ),
        storage_backend="PRIVATE_OBJECT_STORAGE",
        object_key="source-ingestion/synthetic/page-0001.json",
    )

    await repository.create_artifacts(
        ingestion_run_id=_OPERATION_ID,
        artifacts=(artifact,),
    )

    statement = session.execute.await_args.args[0]
    values = session.execute.await_args.args[1]
    assert "INSERT INTO rag_source_ingestion_artifact" in str(statement)
    assert values[0]["ingestion_run_id"] == str(_OPERATION_ID)
    assert values[0]["object_key"] == artifact.object_key
    assert values[0]["raw_checksum"] == artifact.metadata.raw_checksum
    assert "content" not in values[0]
    session.commit.assert_not_awaited()


async def test_snapshot_operation_lock_uses_snapshot_membership() -> None:
    session = AsyncMock(spec=AsyncSession)
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = str(_OPERATION_ID)
    session.execute.return_value = query_result
    repository = SqlAlchemySourceSnapshotRepository(session)

    operation_id = await repository.lock_snapshot_operation(snapshot_id=_SNAPSHOT_ID)

    assert operation_id == _OPERATION_ID
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    where_sql = sql.split("WHERE", maxsplit=1)[1]
    assert "rag_source_operation JOIN rag_source_snapshot" in sql
    assert "rag_source_snapshot.id" in where_sql
    assert "FOR UPDATE" in sql


async def test_snapshot_status_change_is_compare_and_set_without_commit() -> None:
    session = AsyncMock(spec=AsyncSession)
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = str(_SNAPSHOT_ID)
    session.execute.return_value = query_result
    repository = SqlAlchemySourceSnapshotRepository(session)

    changed = await repository.change_snapshot_status(
        snapshot_id=_SNAPSHOT_ID,
        expected_status=SnapshotVerificationStatus.PENDING,
        new_status=SnapshotVerificationStatus.CURRENT,
        verified_at=_NOW,
        effective_at=_NOW,
    )

    assert changed is True
    statement = session.execute.await_args.args[0]
    sql = str(statement)
    where_sql = sql.split("WHERE", maxsplit=1)[1]
    parameters = statement.compile().params
    assert "UPDATE rag_source_snapshot SET" in sql
    assert "rag_source_snapshot.id" in where_sql
    assert "rag_source_snapshot.verification_status" in where_sql
    assert SnapshotVerificationStatus.PENDING in parameters.values()
    assert SnapshotVerificationStatus.CURRENT in parameters.values()
    assert _NOW in parameters.values()
    session.commit.assert_not_awaited()
