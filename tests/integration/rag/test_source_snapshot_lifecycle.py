"""Source Snapshot 저장·현재성 전이를 실제 PostgreSQL에서 검증합니다."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import (
    SqlAlchemySourceSnapshotRepository,
)
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotSelectionDecision,
    fail_snapshot_verification,
    persist_product_ingestion_result,
    select_current_snapshot,
)
from app.core import config
from app.core.db.databases import Base
from app.models.rag_source import (
    RagIngestionRunStatus,
    RagSnapshotVerificationStatus,
    RagSourceIngestionRun,
    RagSourceSnapshot,
    RagSourceSnapshotVerification,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
)

pytestmark = pytest.mark.asyncio

TEST_SCHEMA = "rag_source_snapshot_lifecycle_test"
TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database="test",
)
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
)
session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)

_NOW = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
_CHECKSUM_A = "a" * 64
_CHECKSUM_B = "b" * 64


@pytest_asyncio.fixture(scope="module", autouse=True)
async def isolated_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


async def _seed_operation(suffix: str) -> SourceOperationIdentity:
    identity = SourceOperationIdentity(
        source_code=f"SYNTHETIC_SOURCE_{suffix}",
        endpoint_code=f"SYNTHETIC_ENDPOINT_{suffix}",
        operation_code=f"SYNTHETIC_OPERATION_{suffix}",
    )
    async with session_factory.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(
            RagSourceCreate(source_code=identity.source_code, display_name="Synthetic Source")
        )
        endpoint = await repository.create_endpoint(
            RagSourceEndpointCreate(
                source_id=source.id,
                endpoint_code=identity.endpoint_code,
                display_name="Synthetic Endpoint",
            )
        )
        await repository.create_operation(
            RagSourceOperationCreate(
                endpoint_id=endpoint.id,
                operation_code=identity.operation_code,
                display_name="Synthetic Operation",
            )
        )
    return identity


def _ingestion(identity: SourceOperationIdentity, checksum: str) -> ProductIngestionResult:
    return ProductIngestionResult(
        identity=identity,
        endpoint_receipt_hash="c" * 64,
        raw_manifest_checksum="d" * 64,
        canonical_checksum=checksum,
        canonicalization_spec_version="mfds-product-approval@1",
        record_count=2,
        artifact_count=1,
    )


def _metadata(source_version: str, *, minute: int = 0) -> SnapshotIngestionMetadata:
    started_at = _NOW + timedelta(minutes=minute)
    return SnapshotIngestionMetadata(
        source_version=source_version,
        schema_version="mfds-product-response@1",
        parser_version="mfds-product-parser@1",
        normalization_version="mfds-product-normalization@1",
        rejected_record_count=0,
        run_group_key=f"synthetic-{source_version}-{minute}",
        attempt_number=1,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        collected_at=started_at,
        duration_ms=1000,
        verified_by="synthetic-worker",
    )


async def test_snapshot_history_no_change_conflict_and_restore_are_atomic() -> None:
    identity = await _seed_operation("LIFECYCLE")

    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        first = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:v1", minute=1),
        )
        assert first.snapshot_id is not None
        first_selection = await select_current_snapshot(
            repository=repository,
            snapshot_id=first.snapshot_id,
            selected_at=_NOW + timedelta(minutes=2),
            selected_by="synthetic-reviewer",
        )

        second = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_B),
            metadata=_metadata("external:v2", minute=3),
        )
        assert second.snapshot_id is not None
        second_selection = await select_current_snapshot(
            repository=repository,
            snapshot_id=second.snapshot_id,
            selected_at=_NOW + timedelta(minutes=4),
            selected_by="synthetic-reviewer",
        )

        third = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:v3", minute=5),
        )
        no_change = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:v4", minute=6),
        )
        conflict = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_B),
            metadata=_metadata("external:v3", minute=7),
        )

        restored = await select_current_snapshot(
            repository=repository,
            snapshot_id=first.snapshot_id,
            selected_at=_NOW + timedelta(minutes=8),
            selected_by="synthetic-reviewer",
        )

    assert first_selection.decision is SnapshotSelectionDecision.ACTIVATED
    assert second_selection.replaced_snapshot_id == first.snapshot_id
    assert third.decision is SnapshotIngestionDecision.CREATED
    assert no_change.decision is SnapshotIngestionDecision.NO_CHANGE
    assert no_change.snapshot_id == third.snapshot_id
    assert conflict.decision is SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT
    assert restored.decision is SnapshotSelectionDecision.RESTORED
    assert restored.replaced_snapshot_id == second.snapshot_id

    async with session_factory() as session:
        snapshots = (
            await session.scalars(
                select(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == first.operation_id)
                .order_by(RagSourceSnapshot.collected_at)
            )
        ).all()
        assert len(snapshots) == 3
        assert [snapshot.verification_status for snapshot in snapshots] == [
            RagSnapshotVerificationStatus.CURRENT,
            RagSnapshotVerificationStatus.STALE,
            RagSnapshotVerificationStatus.PENDING,
        ]
        assert snapshots[2].supersedes_snapshot_id == snapshots[1].id

        run_statuses = (
            await session.scalars(
                select(RagSourceIngestionRun.run_status)
                .where(RagSourceIngestionRun.operation_id == first.operation_id)
                .order_by(RagSourceIngestionRun.started_at)
            )
        ).all()
        assert run_statuses == [
            RagIngestionRunStatus.SUCCEEDED,
            RagIngestionRunStatus.SUCCEEDED,
            RagIngestionRunStatus.SUCCEEDED,
            RagIngestionRunStatus.NO_CHANGE,
            RagIngestionRunStatus.FAILED,
        ]
        verification_count = await session.scalar(
            select(func.count())
            .select_from(RagSourceSnapshotVerification)
            .where(RagSourceSnapshotVerification.snapshot_id.in_([snapshot.id for snapshot in snapshots]))
        )
        assert verification_count == 7


async def test_outer_transaction_rollback_removes_snapshot_and_histories() -> None:
    identity = await _seed_operation("ROLLBACK")

    async with session_factory() as session:
        await session.begin()
        repository = SqlAlchemySourceSnapshotRepository(session)
        created = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:rollback", minute=10),
        )
        assert created.snapshot_id is not None
        await session.rollback()

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == created.operation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceIngestionRun)
                .where(RagSourceIngestionRun.run_group_key == "synthetic-external:rollback-10")
            )
            == 0
        )


async def test_failed_snapshot_is_persisted_and_cannot_be_selected() -> None:
    identity = await _seed_operation("FAILED")

    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        created = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:failed", minute=15),
        )
        assert created.snapshot_id is not None
        failed = await fail_snapshot_verification(
            repository=repository,
            snapshot_id=created.snapshot_id,
            failure_code="SCHEMA_DRIFT",
            failed_at=_NOW + timedelta(minutes=16),
            verified_by="synthetic-reviewer",
        )

        with pytest.raises(ValueError, match="FAILED"):
            await select_current_snapshot(
                repository=repository,
                snapshot_id=created.snapshot_id,
                selected_at=_NOW + timedelta(minutes=17),
                selected_by="synthetic-reviewer",
            )

    async with session_factory() as session:
        snapshot = await session.get(RagSourceSnapshot, failed.snapshot_id)
        assert snapshot is not None
        assert snapshot.verification_status is RagSnapshotVerificationStatus.FAILED


async def test_operation_lock_serializes_concurrent_snapshot_decisions() -> None:
    identity = await _seed_operation("CONCURRENCY")

    async with session_factory() as lock_session, session_factory() as waiting_session:
        await lock_session.begin()
        await waiting_session.begin()
        locked_repository = SqlAlchemySourceSnapshotRepository(lock_session)
        waiting_repository = SqlAlchemySourceSnapshotRepository(waiting_session)
        await locked_repository.lock_operation(identity)

        waiting_task = asyncio.create_task(
            persist_product_ingestion_result(
                repository=waiting_repository,
                ingestion=_ingestion(identity, _CHECKSUM_A),
                metadata=_metadata("external:concurrent", minute=20),
            )
        )
        await asyncio.sleep(0.1)
        assert not waiting_task.done()

        await lock_session.commit()
        result = await asyncio.wait_for(waiting_task, timeout=2)
        await waiting_session.commit()

    assert result.decision is SnapshotIngestionDecision.CREATED
