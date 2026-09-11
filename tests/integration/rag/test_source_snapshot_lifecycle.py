"""Source Snapshot 저장·현재성 전이를 실제 PostgreSQL에서 검증합니다."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

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
from ai_worker.tasks.rag.source_client.contracts import (
    RetryDisposition,
    SourceClientFailure,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRequest,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.failure_runs import (
    FailedIngestionRunMetadata,
    IngestionProcessingFailureCode,
    record_processing_failure,
    record_source_run_failure,
)
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.service import (
    SourceAcquisitionInProgressError,
    acquire_source_exclusively,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SNAPSHOT_PUBLICATION_APPROVAL_CHECK,
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotRunRecord,
    SnapshotSelectionDecision,
    SnapshotVerificationStatus,
    fail_snapshot_verification,
    persist_product_ingestion_result,
    select_current_snapshot,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_policy import (
    SourceSnapshotPolicy,
)
from app.core import config
from app.core.db.databases import Base
from app.models.rag_source import (
    RagIngestionRunStatus,
    RagSnapshotVerificationStatus,
    RagSourceIngestionArtifact,
    RagSourceIngestionArtifactKind,
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
    database=config.DB_NAME,
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
_ALLOW_ONE_REJECTION_POLICY = SourceSnapshotPolicy(
    max_rejected_records=1,
    max_rejection_rate=Decimal("0.5"),
)


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


async def _seed_operation(suffix: str, *, product: bool = False) -> SourceOperationIdentity:
    identity = SourceOperationIdentity(
        source_code=f"SYNTHETIC_SOURCE_{suffix}",
        endpoint_code=f"SYNTHETIC_ENDPOINT_{suffix}",
        operation_code=f"SYNTHETIC_OPERATION_{suffix}",
    )
    if product:
        identity = SourceOperationIdentity(
            "MFDS_PRODUCT_APPROVAL", "MFDS_PRODUCT_APPROVAL_API", "LIST_APPROVED_PRODUCTS"
        )
    async with session_factory.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(
            RagSourceCreate(
                source_code=identity.source_code,
                display_name="Synthetic Source",
                max_rejected_records=1,
                max_rejection_rate=Decimal("0.5"),
            )
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


async def _seed_two_operations_for_one_source(
    suffix: str,
) -> tuple[SourceOperationIdentity, SourceOperationIdentity]:
    identities = tuple(
        SourceOperationIdentity(
            source_code=f"SYNTHETIC_SHARED_SOURCE_{suffix}",
            endpoint_code=f"SYNTHETIC_ENDPOINT_{suffix}_{index}",
            operation_code=f"SYNTHETIC_OPERATION_{suffix}_{index}",
        )
        for index in (1, 2)
    )
    async with session_factory.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(
            RagSourceCreate(source_code=identities[0].source_code, display_name="Synthetic Shared Source")
        )
        for identity in identities:
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
    return identities


def _stored_artifacts(*, minute: int = 0) -> tuple[StoredRawArtifact, ...]:
    return (
        StoredRawArtifact(
            page_number=1,
            metadata=RawArtifactMetadata(
                artifact_key="page-0001.json",
                raw_checksum="d" * 64,
                byte_size=128,
                content_type="application/json",
            ),
            storage_backend="PRIVATE_OBJECT_STORAGE",
            object_key=f"source-ingestion/synthetic/run-{minute}/page-0001.json",
        ),
    )


def _ingestion(
    identity: SourceOperationIdentity,
    checksum: str,
    *,
    endpoint_receipt_hash: str = "c" * 64,
) -> ProductIngestionResult:
    artifacts = _stored_artifacts()
    return ProductIngestionResult(
        identity=identity,
        endpoint_receipt_hash=endpoint_receipt_hash,
        raw_manifest_checksum=raw_manifest_checksum(artifact.metadata for artifact in artifacts),
        canonical_checksum=checksum,
        canonicalization_spec_version="mfds-product-approval@1",
        record_count=2,
        artifact_count=1,
    )


def _metadata(source_version: str, *, minute: int = 0) -> SnapshotIngestionMetadata:
    started_at = _NOW + timedelta(minutes=minute)
    external_version = source_version.removeprefix("external:") if source_version.startswith("external:") else None

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
        external_version=external_version,
        duration_ms=1000,
        verified_by="synthetic-worker",
        snapshot_policy=_ALLOW_ONE_REJECTION_POLICY,
    )


async def test_snapshot_history_no_change_conflict_and_restore_are_atomic() -> None:
    identity = await _seed_operation("LIFECYCLE")

    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        first = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:v1", minute=1),
            artifacts=_stored_artifacts(minute=1),
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
            artifacts=_stored_artifacts(minute=3),
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
            artifacts=_stored_artifacts(minute=5),
        )
        no_change = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:v4", minute=6),
            artifacts=_stored_artifacts(minute=6),
        )
        conflict = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_B),
            metadata=_metadata("external:v3", minute=7),
            artifacts=_stored_artifacts(minute=7),
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
        artifact_count = await session.scalar(
            select(func.count())
            .select_from(RagSourceIngestionArtifact)
            .join(RagSourceIngestionRun)
            .where(RagSourceIngestionRun.operation_id == first.operation_id)
        )
        assert artifact_count == 5
        verification_count = await session.scalar(
            select(func.count())
            .select_from(RagSourceSnapshotVerification)
            .where(RagSourceSnapshotVerification.snapshot_id.in_([snapshot.id for snapshot in snapshots]))
        )
        assert verification_count == 9  # Seven validation/selection records and two immutable state seals.


async def test_outer_transaction_rollback_removes_snapshot_and_histories() -> None:
    identity = await _seed_operation("ROLLBACK")

    async with session_factory() as session:
        await session.begin()
        repository = SqlAlchemySourceSnapshotRepository(session)
        created = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:rollback", minute=10),
            artifacts=_stored_artifacts(minute=10),
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
                .select_from(RagSourceIngestionArtifact)
                .join(RagSourceIngestionRun)
                .where(RagSourceIngestionRun.run_group_key == "synthetic-external:rollback-10")
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


async def test_rejection_artifact_is_stored_with_safe_metadata() -> None:
    identity = await _seed_operation("REJECTIONS", product=True)
    raw_artifacts = _stored_artifacts(minute=9)
    rejection = StoredRawArtifact(
        page_number=None,
        metadata=RawArtifactMetadata(
            artifact_key="reject-0001.json",
            raw_checksum="e" * 64,
            byte_size=64,
            content_type="application/json",
        ),
        storage_backend="PRIVATE_OBJECT_STORAGE",
        object_key="source-ingestion/synthetic/reject-0001.json",
        artifact_kind=IngestionArtifactKind.REJECTS,
        reject_code="ITEM_SEQ_REQUIRED",
        parser_location="page[1].record[3]",
    )

    async with session_factory.begin() as session:
        result = await persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=replace(
                _metadata("external:rejections", minute=9),
                rejected_record_count=1,
                parser_version="mfds-product-reject-parser@1",
                reject_code_contract_version="source-reject-codes@1",
            ),
            artifacts=(*raw_artifacts, rejection),
        )

    async with session_factory() as session:
        run = await session.get(RagSourceIngestionRun, result.ingestion_run_id)
        artifacts = (
            await session.scalars(
                select(RagSourceIngestionArtifact).where(
                    RagSourceIngestionArtifact.ingestion_run_id == result.ingestion_run_id
                )
            )
        ).all()

    assert run is not None
    assert run.run_status is RagIngestionRunStatus.FAILED
    assert run.failure_code == "PARSER_VALIDATION_FAILED"
    assert run.snapshot_id is None
    assert len(artifacts) == 2
    stored_rejection = next(
        artifact for artifact in artifacts if artifact.artifact_kind is RagSourceIngestionArtifactKind.REJECTS
    )
    assert stored_rejection.page_number is None
    assert stored_rejection.reject_code == "ITEM_SEQ_REQUIRED"
    assert stored_rejection.parser_location == "page[1].record[3]"


async def test_historical_partial_snapshot_still_requires_publication_approval() -> None:
    identity = await _seed_operation("REJECTION_APPROVAL")
    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        first = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:clean", minute=10),
            artifacts=_stored_artifacts(minute=10),
        )
        rejected = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_B),
            metadata=_metadata("external:rejected", minute=11),
            artifacts=_stored_artifacts(minute=11),
        )
        assert rejected.snapshot_id is not None
        # Seed a historical partial Snapshot; v1 identity rejection no longer creates one.
        await session.execute(
            text("UPDATE rag_source_snapshot SET rejected_record_count=1 WHERE id=:id"),
            {"id": str(rejected.snapshot_id)},
        )
        with pytest.raises(ValueError, match="publication 승인"):
            await select_current_snapshot(
                repository=repository,
                snapshot_id=rejected.snapshot_id,
                selected_at=_NOW + timedelta(minutes=12),
                selected_by="synthetic-reviewer",
            )
        await repository.append_verification(
            snapshot_id=rejected.snapshot_id,
            check_name=SNAPSHOT_PUBLICATION_APPROVAL_CHECK,
            result="PASSED",
            verified_at=_NOW + timedelta(minutes=13),
            verified_by="synthetic-reviewer",
        )
        selected = await select_current_snapshot(
            repository=repository,
            snapshot_id=rejected.snapshot_id,
            selected_at=_NOW + timedelta(minutes=14),
            selected_by="synthetic-reviewer",
        )

    assert first.decision is SnapshotIngestionDecision.CREATED
    assert rejected.decision is SnapshotIngestionDecision.CREATED
    assert selected.decision is SnapshotSelectionDecision.ACTIVATED


async def test_failed_snapshot_is_persisted_and_cannot_be_selected() -> None:
    identity = await _seed_operation("FAILED")

    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        created = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:failed", minute=15),
            artifacts=_stored_artifacts(minute=15),
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


async def test_failed_snapshot_can_be_retried_with_same_source_version() -> None:
    identity = await _seed_operation("FAILED_RETRY")

    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        first = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A, endpoint_receipt_hash="1" * 64),
            metadata=_metadata("external:retry", minute=18),
            artifacts=_stored_artifacts(minute=18),
        )
        assert first.snapshot_id is not None
        await fail_snapshot_verification(
            repository=repository,
            snapshot_id=first.snapshot_id,
            failure_code="SCHEMA_DRIFT",
            failed_at=_NOW + timedelta(minutes=19),
            verified_by="synthetic-reviewer",
        )
        retried = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A, endpoint_receipt_hash="1" * 64),
            metadata=replace(
                _metadata("external:retry", minute=20),
                run_group_key="synthetic-external:retry-second",
            ),
            artifacts=_stored_artifacts(minute=20),
        )

    assert retried.decision is SnapshotIngestionDecision.CREATED
    assert retried.snapshot_id != first.snapshot_id
    async with session_factory() as session:
        snapshots = (
            await session.scalars(
                select(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == first.operation_id)
                .order_by(RagSourceSnapshot.collected_at)
            )
        ).all()
    assert [snapshot.verification_status for snapshot in snapshots] == [
        RagSnapshotVerificationStatus.FAILED,
        RagSnapshotVerificationStatus.PENDING,
    ]
    assert [snapshot.endpoint_receipt_hash for snapshot in snapshots] == ["1" * 64, "1" * 64]


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
                artifacts=_stored_artifacts(minute=20),
            )
        )
        await asyncio.sleep(0.1)
        assert not waiting_task.done()

        await lock_session.commit()
        result = await asyncio.wait_for(waiting_task, timeout=2)
        await waiting_session.commit()

    assert result.decision is SnapshotIngestionDecision.CREATED


class _BlockingSourceClient:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_all_pages(self, request: SourceRequest) -> SourceRunResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return SourceRunResult(
            operation=request.operation,
            status=SourceRunStatus.SUCCEEDED,
            pages=(),
            failure=None,
        )


async def test_acquisition_lock_allows_only_one_concurrent_provider_call() -> None:
    identity = await _seed_operation("ACQUISITION_LOCK")
    request = SourceRequest(operation=identity, parameters={})
    client = _BlockingSourceClient()

    async with session_factory() as first_session, session_factory() as second_session:
        await first_session.begin()
        await second_session.begin()
        first_task = asyncio.create_task(
            acquire_source_exclusively(
                gate=SqlAlchemySourceSnapshotRepository(first_session),
                client=client,
                request=request,
            )
        )
        await asyncio.wait_for(client.started.wait(), timeout=2)

        try:
            with pytest.raises(SourceAcquisitionInProgressError):
                await acquire_source_exclusively(
                    gate=SqlAlchemySourceSnapshotRepository(second_session),
                    client=client,
                    request=request,
                )
        finally:
            client.release.set()

        result = await asyncio.wait_for(first_task, timeout=2)
        await first_session.commit()
        await second_session.rollback()

    assert result.operation == identity
    assert client.calls == 1


async def test_acquisition_lock_is_shared_by_different_operations_of_one_source() -> None:
    first_identity, second_identity = await _seed_two_operations_for_one_source("ACQUISITION_LOCK")
    client = _BlockingSourceClient()

    async with session_factory() as first_session, session_factory() as second_session:
        await first_session.begin()
        await second_session.begin()
        first_task = asyncio.create_task(
            acquire_source_exclusively(
                gate=SqlAlchemySourceSnapshotRepository(first_session),
                client=client,
                request=SourceRequest(operation=first_identity, parameters={}),
            )
        )
        await asyncio.wait_for(client.started.wait(), timeout=2)

        try:
            with pytest.raises(SourceAcquisitionInProgressError):
                await acquire_source_exclusively(
                    gate=SqlAlchemySourceSnapshotRepository(second_session),
                    client=client,
                    request=SourceRequest(operation=second_identity, parameters={}),
                )
        finally:
            client.release.set()

        await asyncio.wait_for(first_task, timeout=2)
        await first_session.commit()
        await second_session.rollback()

    assert client.calls == 1


async def test_failures_before_snapshot_are_recorded_without_snapshot() -> None:
    identity = await _seed_operation("PRE_SNAPSHOT_FAILURE")
    metadata = FailedIngestionRunMetadata(
        run_group_key="synthetic-pre-snapshot-failure",
        attempt_number=1,
        started_at=_NOW + timedelta(minutes=20),
        finished_at=_NOW + timedelta(minutes=20, seconds=1),
        duration_ms=1000,
    )
    source_failure = SourceRunResult(
        operation=identity,
        status=SourceRunStatus.FAILED,
        pages=(),
        failure=SourceClientFailure(
            code=SourceFailureCode.TIMEOUT,
            retry=RetryDisposition.BACKOFF,
            safe_message="Synthetic timeout.",
        ),
    )

    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        await record_source_run_failure(
            repository=repository,
            result=source_failure,
            metadata=metadata,
        )
        await record_processing_failure(
            repository=repository,
            identity=identity,
            metadata=replace(metadata, attempt_number=2),
            failure_code=IngestionProcessingFailureCode.PARSER_VALIDATION_FAILED,
            artifacts=_stored_artifacts(minute=20),
        )

    async with session_factory() as session:
        runs = (
            await session.scalars(
                select(RagSourceIngestionRun)
                .where(RagSourceIngestionRun.run_group_key == metadata.run_group_key)
                .order_by(RagSourceIngestionRun.attempt_number)
            )
        ).all()
        snapshot_count = await session.scalar(
            select(func.count())
            .select_from(RagSourceSnapshot)
            .where(RagSourceSnapshot.operation_id == runs[0].operation_id)
        )
        artifact_count = await session.scalar(
            select(func.count())
            .select_from(RagSourceIngestionArtifact)
            .where(RagSourceIngestionArtifact.ingestion_run_id == runs[1].id)
        )

    assert [run.run_status for run in runs] == [
        RagIngestionRunStatus.FAILED,
        RagIngestionRunStatus.FAILED,
    ]
    assert [run.failure_code for run in runs] == ["TIMEOUT", "PARSER_VALIDATION_FAILED"]
    assert all(run.snapshot_id is None for run in runs)
    assert snapshot_count == 0
    assert artifact_count == 1


@pytest.mark.parametrize("changed", ["content", "receipt", "parser"])
async def test_failed_same_version_different_contract_is_conflict(changed: str) -> None:
    identity = await _seed_operation(f"FAILED_CONFLICT_{changed}")
    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        ingestion = _ingestion(identity, _CHECKSUM_A, endpoint_receipt_hash="1" * 64)
        first = await persist_product_ingestion_result(
            repository=repository,
            ingestion=ingestion,
            metadata=_metadata("external:conflict", minute=30),
            artifacts=_stored_artifacts(minute=30),
        )
        assert first.snapshot_id is not None
        await fail_snapshot_verification(
            repository=repository,
            snapshot_id=first.snapshot_id,
            failure_code="SCHEMA_DRIFT",
            failed_at=_NOW + timedelta(minutes=31),
            verified_by="synthetic-reviewer",
        )
        retry_metadata = _metadata("external:conflict", minute=32)
        if changed == "content":
            ingestion = replace(ingestion, canonical_checksum=_CHECKSUM_B)
        elif changed == "receipt":
            ingestion = replace(ingestion, endpoint_receipt_hash="2" * 64)
        else:
            retry_metadata = replace(retry_metadata, parser_version="parser-v2")
        result = await persist_product_ingestion_result(
            repository=repository,
            ingestion=ingestion,
            metadata=replace(retry_metadata, run_group_key=f"synthetic-conflict-{changed}"),
            artifacts=_stored_artifacts(minute=32),
        )
        assert result.decision is SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT
        assert result.snapshot_id is None
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(RagSourceSnapshot)
            .where(RagSourceSnapshot.operation_id == first.operation_id)
        )
        assert count == 1
        run = await session.get(RagSourceIngestionRun, result.ingestion_run_id)
        assert run is not None
        assert run.failure_code == "SOURCE_VERSION_CONFLICT"


async def test_python_transition_rolls_back_when_selection_audit_fails(monkeypatch) -> None:
    identity = await _seed_operation("AUDIT_ROLLBACK_398")
    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        created = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:audit398", minute=1),
            artifacts=_stored_artifacts(minute=1),
        )
        assert created.snapshot_id is not None
        snapshot_id = created.snapshot_id
    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        monkeypatch.setattr(repository, "append_verification", AsyncMock(side_effect=RuntimeError("audit unavailable")))
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await repository.change_snapshot_status(
                snapshot_id=snapshot_id,
                expected_status=SnapshotVerificationStatus.PENDING,
                new_status=SnapshotVerificationStatus.CURRENT,
                verified_at=_NOW,
                effective_at=_NOW,
                selected_by="synthetic-reviewer",
            )
        # 호출자가 예외를 잡고 외부 transaction을 commit해도 상태 변경만 남지 않습니다.
    async with session_factory() as session:
        snapshot = await session.get(RagSourceSnapshot, snapshot_id)
        assert snapshot is not None
        assert snapshot.verification_status == RagSnapshotVerificationStatus.PENDING
        assert snapshot.verified_at is None
        count = await session.scalar(
            select(func.count())
            .select_from(RagSourceSnapshotVerification)
            .where(
                RagSourceSnapshotVerification.snapshot_id == snapshot_id,
                RagSourceSnapshotVerification.check_name == "snapshot-current-selection",
            )
        )
        assert count == 0


@pytest.mark.parametrize("scenario", ["success", "checksum_mismatch", "request_audit_failure"])
async def test_writer_selection_transaction(scenario, monkeypatch):
    from ai_worker.admin.source_writer import select_snapshot

    identity = await _seed_operation(f"WRITER_{scenario}")
    async with session_factory.begin() as session:
        created = await persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata(f"external:writer-{scenario}", minute=1),
            artifacts=_stored_artifacts(minute=1),
        )
    assert created.snapshot_id is not None
    original = SqlAlchemySourceSnapshotRepository.append_verification

    async def append(repository, **kwargs):
        if kwargs["check_name"] == "snapshot-selection-request":
            raise RuntimeError("synthetic audit failure")
        return await original(repository, **kwargs)

    if scenario == "request_audit_failure":
        monkeypatch.setattr(SqlAlchemySourceSnapshotRepository, "append_verification", append)

    async def execute():
        async with session_factory.begin() as session:
            return await select_snapshot(
                session,
                snapshot_id=created.snapshot_id,
                expected_checksum=_CHECKSUM_B if scenario == "checksum_mismatch" else _CHECKSUM_A,
                actor="synthetic-operator",
                reason_code="VERIFIED_RELEASE",
            )

    if scenario == "success":
        assert (await execute()).decision is SnapshotSelectionDecision.ACTIVATED
        assert (await execute()).decision is SnapshotSelectionDecision.ALREADY_CURRENT
    else:
        with pytest.raises((ValueError, RuntimeError)):
            await execute()
    async with session_factory() as session:
        snapshot = await session.get(RagSourceSnapshot, created.snapshot_id)
        assert snapshot.verification_status == (
            RagSnapshotVerificationStatus.CURRENT if scenario == "success" else RagSnapshotVerificationStatus.PENDING
        )
        audits = (
            (
                await session.execute(
                    select(RagSourceSnapshotVerification.check_name).where(
                        RagSourceSnapshotVerification.snapshot_id == created.snapshot_id,
                        RagSourceSnapshotVerification.check_name.in_(
                            ("snapshot-current-selection", "snapshot-selection-request")
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(audits) == (
            ["snapshot-current-selection", "snapshot-selection-request"] if scenario == "success" else []
        )


async def test_writer_entrypoint_with_actual_restricted_credentials():
    from argparse import Namespace
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    from ai_worker.admin.source_writer import WriterConfig, run_selection
    from infra.python.source_role_policy import apply_source_role_policy

    suffix = uuid4().hex[:12]
    runtime, writer = f"source398_reader_{suffix}", f"source398_writer_{suffix}"
    password = "synthetic-source398-test-only"
    admin = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    identity = await _seed_operation(f"ENTRYPOINT_{suffix}")
    async with session_factory.begin() as session:
        created = await persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata(f"external:entrypoint-{suffix}", minute=1),
            artifacts=_stored_artifacts(minute=1),
        )
    args = Namespace(snapshot_id=created.snapshot_id, expected_checksum=_CHECKSUM_A, reason_code="VERIFIED_RELEASE")
    try:
        async with admin.begin() as connection:
            for role in (runtime, writer):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
                await connection.execute(text(f'ALTER ROLE "{role}" SET search_path TO {TEST_SCHEMA}'))
            await apply_source_role_policy(
                connection, schema=TEST_SCHEMA, owner=config.DB_USER, runtime=runtime, writer=writer
            )
        writer_config = WriterConfig(TEST_DATABASE_URL.set(username=writer, password=password), "synthetic-operator")
        reader_config = WriterConfig(TEST_DATABASE_URL.set(username=runtime, password=password), "synthetic-operator")
        with pytest.raises(ValueError, match="non-owner"):
            await run_selection(WriterConfig(TEST_DATABASE_URL, "synthetic-operator"), args)
        async with admin.begin() as connection:
            await connection.execute(text(f'ALTER ROLE "{writer}" REPLICATION'))
        with pytest.raises(ValueError, match="non-owner"):
            await run_selection(writer_config, args)
        async with admin.begin() as connection:
            await connection.execute(text(f'ALTER ROLE "{writer}" NOREPLICATION'))
        with pytest.raises(DBAPIError):
            await run_selection(reader_config, args)
        async with admin.begin() as connection:
            await connection.execute(
                text(f'REVOKE INSERT ON {TEST_SCHEMA}.rag_source_snapshot_verification FROM "{writer}"')
            )
        with pytest.raises(DBAPIError):
            await run_selection(writer_config, args)
        async with session_factory() as session:
            snapshot = await session.get(RagSourceSnapshot, created.snapshot_id)
            assert snapshot.verification_status == RagSnapshotVerificationStatus.PENDING
        async with admin.begin() as connection:
            await connection.execute(
                text(f'GRANT INSERT ON {TEST_SCHEMA}.rag_source_snapshot_verification TO "{writer}"')
            )
        assert (await run_selection(writer_config, args)).decision is SnapshotSelectionDecision.ACTIVATED
        assert (await run_selection(writer_config, args)).decision is SnapshotSelectionDecision.ALREADY_CURRENT
        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshotVerification)
                .where(
                    RagSourceSnapshotVerification.snapshot_id == created.snapshot_id,
                    RagSourceSnapshotVerification.check_name == "snapshot-selection-request",
                )
            )
            assert count == 1
    finally:
        async with admin.begin() as connection:
            for role in (runtime, writer):
                if await connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                    await connection.execute(text(f'DROP OWNED BY "{role}"'))
                    await connection.execute(text(f'DROP ROLE "{role}"'))
        await admin.dispose()


@pytest.mark.parametrize("writer_path", ["worker", "backend"])
async def test_verification_insert_waits_for_operation_transition_lock(writer_path):
    from sqlalchemy.exc import DBAPIError

    from app.models.rag_source import RagVerificationResultStatus
    from app.repositories.rag_source_catalog_repository import RagSourceSnapshotVerificationCreate

    identity = await _seed_operation(f"VERIFY_LOCK_{writer_path}")
    async with session_factory.begin() as session:
        created = await persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata(f"external:verify-lock-{writer_path}", minute=1),
            artifacts=_stored_artifacts(minute=1),
        )
    assert created.snapshot_id is not None
    async with session_factory.begin() as holder:
        await SqlAlchemySourceSnapshotRepository(holder).lock_snapshot_operation(snapshot_id=created.snapshot_id)
        with pytest.raises(DBAPIError) as error:
            async with session_factory.begin() as contender:
                await contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
                if writer_path == "worker":
                    await SqlAlchemySourceSnapshotRepository(contender).append_verification(
                        snapshot_id=created.snapshot_id,
                        check_name="synthetic-lock-check",
                        result="PASSED",
                        verified_at=_NOW,
                        verified_by="synthetic-reviewer",
                    )
                else:
                    await RagSourceCatalogRepository(contender).create_snapshot_verification(
                        RagSourceSnapshotVerificationCreate(
                            snapshot_id=created.snapshot_id,
                            check_name="synthetic-lock-check",
                            verification_result=RagVerificationResultStatus.PASSED,
                            verified_at=_NOW,
                            verified_by="synthetic-reviewer",
                        )
                    )
        assert error.value.orig.sqlstate == "55P03"
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshotVerification)
                .where(
                    RagSourceSnapshotVerification.snapshot_id == created.snapshot_id,
                    RagSourceSnapshotVerification.check_name == "synthetic-lock-check",
                )
            )
            == 0
        )


async def test_writer_login_can_acquire_and_persist_without_source_update_privilege() -> None:
    from uuid import uuid4

    from infra.python.source_role_policy import apply_source_role_policy

    suffix = uuid4().hex[:10]
    runtime, writer = f"acquire_reader_{suffix}", f"acquire_writer_{suffix}"
    password = "synthetic-acquisition-only"
    identity = await _seed_operation(f"WRITER_{suffix}")
    admin = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    producer = create_async_engine(
        TEST_DATABASE_URL.set(username=writer, password=password),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
        hide_parameters=True,
    )
    try:
        async with admin.begin() as connection:
            for role in (runtime, writer):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            await apply_source_role_policy(
                connection, schema=TEST_SCHEMA, owner=config.DB_USER, runtime=runtime, writer=writer
            )
            assert not await connection.scalar(
                text("SELECT has_any_column_privilege(:role, :table, 'UPDATE')"),
                {"role": writer, "table": f"{TEST_SCHEMA}.rag_source"},
            )
        factory = async_sessionmaker(producer)
        async with factory.begin() as first:
            repository = SqlAlchemySourceSnapshotRepository(first)
            await repository.try_lock_acquisition(identity)
            async with factory.begin() as second:
                with pytest.raises(SourceAcquisitionInProgressError):
                    await SqlAlchemySourceSnapshotRepository(second).try_lock_acquisition(identity)
            result = await persist_product_ingestion_result(
                repository=repository,
                ingestion=_ingestion(identity, _CHECKSUM_A),
                metadata=_metadata("external:review-429"),
                artifacts=_stored_artifacts(),
            )
        async with factory.begin() as second:
            await SqlAlchemySourceSnapshotRepository(second).try_lock_acquisition(identity)
            assert (
                await second.scalar(
                    select(func.count())
                    .select_from(RagSourceSnapshot)
                    .where(RagSourceSnapshot.source_version == "external:review-429")
                )
                == 1
            )
        assert result.snapshot_id is not None
    finally:
        await producer.dispose()
        async with admin.begin() as connection:
            for role in (runtime, writer):
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE "{role}"'))
        await admin.dispose()


async def test_external_version_and_source_policy_survive_database_roundtrip() -> None:
    identity = await _seed_operation("POLICY_ROUNDTRIP")
    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        result = await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:provider-release-1"),
            artifacts=_stored_artifacts(),
        )
    async with session_factory() as session:
        snapshot = await session.get(RagSourceSnapshot, result.snapshot_id)
        assert snapshot is not None
        assert snapshot.external_version == "provider-release-1"
        assert snapshot.source_version == "external:provider-release-1"
        policy = await SqlAlchemySourceSnapshotRepository(session).get_source_policy(operation_id=result.operation_id)
        assert policy == _ALLOW_ONE_REJECTION_POLICY


async def test_no_change_version_observation_is_preserved_and_detects_later_conflict() -> None:
    identity = await _seed_operation("ATTEMPT_VERSION")
    results = []
    for minute, (version, checksum) in enumerate(
        (
            ("external:first", _CHECKSUM_A),
            ("external:observed", _CHECKSUM_A),
            ("external:observed", _CHECKSUM_B),
        )
    ):
        async with session_factory.begin() as session:
            results.append(
                await persist_product_ingestion_result(
                    repository=SqlAlchemySourceSnapshotRepository(session),
                    ingestion=_ingestion(identity, checksum),
                    metadata=_metadata(version, minute=minute),
                    artifacts=_stored_artifacts(minute=minute),
                )
            )
    created, unchanged, conflict = results
    assert unchanged.decision is SnapshotIngestionDecision.NO_CHANGE
    assert unchanged.snapshot_id == created.snapshot_id
    assert conflict.decision is SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT
    assert conflict.snapshot_id is None
    async with session_factory() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        for result in (created, unchanged, conflict):
            receipt = await repository.get_attempt_receipt(ingestion_run_id=result.ingestion_run_id)
            assert receipt is not None
            assert receipt.decision is result.decision
        attempt = await repository.get_attempt_receipt(ingestion_run_id=unchanged.ingestion_run_id)
        assert attempt is not None
        assert attempt.attempted_source_version == "external:observed"
        assert attempt.attempted_external_version == "observed"
        assert attempt.attempted_canonical_contract["canonical_checksum"] == _CHECKSUM_A
        snapshot = await session.get(RagSourceSnapshot, created.snapshot_id)
        assert snapshot.source_version == "external:first"
        failed = await repository.get_attempt_receipt(ingestion_run_id=conflict.ingestion_run_id)
        assert failed.attempted_canonical_contract["canonical_checksum"] == _CHECKSUM_B
        assert failed.failure_code == "SOURCE_VERSION_CONFLICT"


@pytest.mark.parametrize("invalid_version", ["v" * 201, "v" * 202, "v" * 300, "합" * 201, "SYNTHETIC_SECRET\ninvalid"])
async def test_invalid_version_attempt_stores_only_digest_length_and_safe_code(invalid_version: str, caplog) -> None:
    import hashlib
    from unittest.mock import Mock
    from uuid import uuid4

    from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactStore
    from ai_worker.tasks.rag.source_ingestion.persistence import preserve_and_persist_product_ingestion_result

    identity = await _seed_operation(f"INVALID_{uuid4().hex[:8]}")
    store = Mock(spec=RawArtifactStore)
    metadata = replace(_metadata("external:valid"), source_version=invalid_version, external_version=None)
    assert invalid_version not in repr(metadata)
    async with session_factory.begin() as session:
        failed = await preserve_and_persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            artifact_store=store,
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=metadata,
            raw_artifacts=(),
        )
    assert failed.decision is SnapshotIngestionDecision.VALIDATION_FAILED
    assert failed.failure_code == "SOURCE_VERSION_INVALID"
    assert not store.mock_calls
    async with session_factory() as session:
        attempt = await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
            ingestion_run_id=failed.ingestion_run_id
        )
        assert attempt is not None
        assert attempt.snapshot_id is None
        assert attempt.run_status == "FAILED"
        assert attempt.decision is SnapshotIngestionDecision.VALIDATION_FAILED
        assert attempt.attempted_source_version is None
        assert attempt.attempted_external_version is None
        assert attempt.invalid_source_version_sha256 == hashlib.sha256(invalid_version.encode()).hexdigest()
        assert attempt.invalid_source_version_byte_length == len(invalid_version.encode())
        assert attempt.validation_reason_code == "SOURCE_VERSION_INVALID"
        assert invalid_version not in repr(attempt)
        assert invalid_version not in caplog.text
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == failed.operation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceIngestionArtifact)
                .where(RagSourceIngestionArtifact.ingestion_run_id == failed.ingestion_run_id)
            )
            == 0
        )


async def test_attempt_is_rolled_back_with_the_snapshot_transaction() -> None:
    identity = await _seed_operation("ATTEMPT_ROLLBACK")
    async with session_factory() as session:
        transaction = await session.begin()
        result = await persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:rolled-back"),
            artifacts=_stored_artifacts(),
        )
        await transaction.rollback()
    async with session_factory() as session:
        assert (
            await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
                ingestion_run_id=result.ingestion_run_id
            )
            is None
        )
        assert await session.get(RagSourceSnapshot, result.snapshot_id) is None


async def test_snapshot_receipt_keeps_identity_and_blocks_tampered_external_version_selection() -> None:
    from ai_worker.admin.source_writer import select_snapshot
    from ai_worker.tasks.rag.source_ingestion.source_version import SourceVersionValidationError

    identity = await _seed_operation("RECEIPT_BINDING")
    async with session_factory.begin() as session:
        result = await persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            ingestion=_ingestion(identity, _CHECKSUM_A),
            metadata=_metadata("external:receipt-version"),
            artifacts=_stored_artifacts(),
        )
    async with session_factory.begin() as session:
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_snapshot_receipt(snapshot_id=result.snapshot_id)
        assert receipt is not None
        receipt.validate_provenance()
        assert receipt.source_code == identity.source_code
        assert receipt.source_snapshot_id == result.snapshot_id
        assert receipt.endpoint_receipt_hash == "c" * 64
        assert receipt.verification_status is SnapshotVerificationStatus.PENDING
        snapshot = await session.get(RagSourceSnapshot, result.snapshot_id)
        snapshot.external_version = "tampered"
    async with session_factory.begin() as session:
        with pytest.raises(SourceVersionValidationError):
            await select_snapshot(
                session,
                snapshot_id=result.snapshot_id,
                expected_checksum=_CHECKSUM_A,
                actor="synthetic-reviewer",
                reason_code="VERIFIED_RELEASE",
            )
    async with session_factory() as session:
        snapshot = await session.get(RagSourceSnapshot, result.snapshot_id)
        assert snapshot.verification_status is RagSnapshotVerificationStatus.PENDING


async def test_attempt_receipt_rejects_unmapped_database_failure_code() -> None:
    identity = await _seed_operation("UNMAPPED_RECEIPT")
    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        operation_id = await repository.lock_operation(identity)
        run_id = await repository.create_run(
            SnapshotRunRecord(
                operation_id=operation_id,
                snapshot_id=None,
                run_group_key="unmapped-receipt",
                attempt_number=1,
                run_status="FAILED",
                started_at=_NOW,
                finished_at=_NOW,
                duration_ms=0,
                failure_code="FUTURE_FAILURE",
            )
        )
    async with session_factory() as session:
        with pytest.raises(ValueError, match="decision is unavailable"):
            await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(ingestion_run_id=run_id)


@pytest.mark.parametrize("code", list(SourceFailureCode))
async def test_collection_failure_recording_roundtrips_receipt(code) -> None:
    from ai_worker.tasks.rag.source_ingestion.failure_codes import COLLECTION_EMPTY_RESULT

    identity = await _seed_operation(f"COLLECTION_{code.value}")
    metadata = FailedIngestionRunMetadata("synthetic-collection", 1, _NOW, _NOW)
    result = SourceRunResult(
        operation=identity,
        status=SourceRunStatus.FAILED,
        pages=(),
        failure=SourceClientFailure(code=code, retry=RetryDisposition.BACKOFF, safe_message="Synthetic failure."),
    )
    async with session_factory.begin() as session:
        failed = await record_source_run_failure(
            repository=SqlAlchemySourceSnapshotRepository(session),
            result=result,
            metadata=metadata,
        )
    async with session_factory() as session:
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
            ingestion_run_id=failed.ingestion_run_id
        )
        assert receipt is not None
        assert receipt.decision is SnapshotIngestionDecision.COLLECTION_FAILED
        assert receipt.failure_code == code.value
        assert receipt.snapshot_id is None
        assert receipt.validation_reason_code == (
            COLLECTION_EMPTY_RESULT if code is SourceFailureCode.EMPTY_RESULT else None
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == receipt.operation_id)
            )
            == 0
        )


@pytest.mark.parametrize("empty", [True, False])
async def test_snapshot_policy_failure_or_unversioned_rejection_is_fail_closed(empty) -> None:
    from ai_worker.tasks.rag.source_ingestion.failure_codes import SNAPSHOT_POLICY_EMPTY_RESULT

    identity = await _seed_operation(f"POLICY_{empty}")
    ingestion = replace(_ingestion(identity, _CHECKSUM_A), record_count=0 if empty else 2)
    metadata = replace(
        _metadata("external:policy"), rejected_record_count=0 if empty else 1, snapshot_policy=SourceSnapshotPolicy()
    )
    artifacts = _stored_artifacts()
    if not empty:
        artifacts += (
            StoredRawArtifact(
                page_number=None,
                metadata=RawArtifactMetadata("synthetic-policy-reject.json", "e" * 64, 64, "application/json"),
                storage_backend="PRIVATE_OBJECT_STORAGE",
                object_key="source-ingestion/synthetic/policy-reject.json",
                artifact_kind=IngestionArtifactKind.REJECTS,
                reject_code="MISSING_ITEM_SEQ",
                parser_location="page[1].record[0]",
            ),
        )
    if not empty:
        # #444 forbids writing REJECTS without the product reject contract. Valid
        # processing failures of both kinds are covered in test_reject_contract_165.
        from ai_worker.tasks.rag.source_ingestion.reject_codes import RejectContractError

        with pytest.raises(RejectContractError):
            async with session_factory.begin() as session:
                await persist_product_ingestion_result(
                    repository=SqlAlchemySourceSnapshotRepository(session),
                    ingestion=ingestion,
                    metadata=metadata,
                    artifacts=artifacts,
                )
        async with session_factory() as session:
            operation_id = await SqlAlchemySourceSnapshotRepository(session).lock_operation(identity)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RagSourceIngestionRun)
                    .where(RagSourceIngestionRun.operation_id == operation_id)
                )
                == 0
            )
        return
    async with session_factory.begin() as session:
        failed = await persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            ingestion=ingestion,
            metadata=metadata,
            artifacts=artifacts,
        )
    async with session_factory() as session:
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
            ingestion_run_id=failed.ingestion_run_id
        )
        assert receipt is not None
        assert receipt.decision is SnapshotIngestionDecision.VALIDATION_FAILED
        assert receipt.failure_code == ("EMPTY_RESULT" if empty else "REJECTION_LIMIT_EXCEEDED")
        assert receipt.validation_reason_code == (SNAPSHOT_POLICY_EMPTY_RESULT if empty else None)
        assert receipt.snapshot_id is None


async def test_legacy_empty_result_remains_available_as_audit_without_guessed_decision() -> None:
    identity = await _seed_operation("LEGACY_EMPTY")
    async with session_factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        operation_id = await repository.lock_operation(identity)
        run_id = await repository.create_run(
            SnapshotRunRecord(
                operation_id=operation_id,
                snapshot_id=None,
                run_group_key="synthetic-legacy-empty",
                attempt_number=1,
                run_status="FAILED",
                started_at=_NOW,
                finished_at=_NOW,
                duration_ms=0,
                failure_code="EMPTY_RESULT",
            )
        )
    async with session_factory() as session:
        with pytest.raises(ValueError, match="decision is unavailable"):
            await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(ingestion_run_id=run_id)
        row = await session.get(RagSourceIngestionRun, run_id)
        assert row is not None and row.failure_code == "EMPTY_RESULT" and row.validation_reason_code is None
