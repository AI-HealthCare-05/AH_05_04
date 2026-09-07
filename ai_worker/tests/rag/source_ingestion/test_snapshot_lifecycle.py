from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SOURCE_VERSION_CONFLICT,
    SnapshotCreateRequest,
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotReference,
    SnapshotRunRecord,
    persist_product_ingestion_result,
)

_OPERATION_ID = UUID("11111111-1111-4111-8111-111111111111")
_CHECKSUM_A = "a" * 64
_CHECKSUM_B = "b" * 64
_NOW = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)


class FakeSnapshotRepository:
    def __init__(self) -> None:
        self.snapshots: list[SnapshotReference] = []
        self.create_requests: list[SnapshotCreateRequest] = []
        self.verifications: list[tuple[UUID, str]] = []
        self.runs: list[SnapshotRunRecord] = []
        self.locked_identities: list[SourceOperationIdentity] = []

    async def lock_operation(self, identity: SourceOperationIdentity) -> UUID:
        self.locked_identities.append(identity)
        return _OPERATION_ID

    async def get_snapshot_by_version(
        self,
        *,
        operation_id: UUID,
        source_version: str,
    ) -> SnapshotReference | None:
        assert operation_id == _OPERATION_ID
        return next((item for item in self.snapshots if item.source_version == source_version), None)

    async def get_latest_snapshot(self, *, operation_id: UUID) -> SnapshotReference | None:
        assert operation_id == _OPERATION_ID
        return self.snapshots[-1] if self.snapshots else None

    async def create_snapshot(self, request: SnapshotCreateRequest) -> UUID:
        snapshot_id = uuid4()
        self.create_requests.append(request)
        self.snapshots.append(
            SnapshotReference(
                snapshot_id=snapshot_id,
                source_version=request.metadata.source_version,
                canonical_checksum=request.ingestion.canonical_checksum,
                schema_version=request.metadata.schema_version,
                parser_version=request.metadata.parser_version,
                normalization_version=request.metadata.normalization_version,
                canonicalization_spec_version=request.ingestion.canonicalization_spec_version,
            )
        )
        return snapshot_id

    async def append_verification(
        self,
        *,
        snapshot_id: UUID,
        result: str,
        verified_at: datetime,
        verified_by: str | None,
    ) -> None:
        assert verified_at == _NOW + timedelta(seconds=1)
        assert verified_by == "source-ingestion-worker"
        self.verifications.append((snapshot_id, result))

    async def create_run(self, record: SnapshotRunRecord) -> None:
        self.runs.append(record)


def _ingestion(checksum: str = _CHECKSUM_A) -> ProductIngestionResult:
    return ProductIngestionResult(
        identity=SourceOperationIdentity(
            source_code="MFDS_PRODUCT_APPROVAL",
            endpoint_code="MFDS_PRODUCT_APPROVAL_API",
            operation_code="LIST_APPROVED_PRODUCTS",
        ),
        endpoint_receipt_hash="c" * 64,
        raw_manifest_checksum="d" * 64,
        canonical_checksum=checksum,
        canonicalization_spec_version="mfds-product-approval@1",
        record_count=2,
        artifact_count=1,
    )


def _metadata(source_version: str) -> SnapshotIngestionMetadata:
    return SnapshotIngestionMetadata(
        source_version=source_version,
        schema_version="mfds-product-response@1",
        parser_version="mfds-product-parser@1",
        normalization_version="mfds-product-normalization@1",
        rejected_record_count=0,
        run_group_key=f"synthetic-{source_version}",
        attempt_number=1,
        started_at=_NOW,
        finished_at=_NOW + timedelta(seconds=1),
        collected_at=_NOW,
        duration_ms=1000,
        verified_by="source-ingestion-worker",
    )


async def test_first_result_creates_pending_snapshot_candidate_and_success_history() -> None:
    repository = FakeSnapshotRepository()

    result = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
    )

    assert result.decision is SnapshotIngestionDecision.CREATED
    assert result.snapshot_id == repository.snapshots[0].snapshot_id
    assert repository.create_requests[0].supersedes_snapshot_id is None
    assert repository.verifications == [(result.snapshot_id, "PASSED")]
    assert repository.runs[0].run_status == "SUCCEEDED"
    assert repository.runs[0].snapshot_id == result.snapshot_id


async def test_same_content_with_new_version_appends_no_change_to_latest_snapshot() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
    )

    repeated = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v2"),
    )

    assert repeated.decision is SnapshotIngestionDecision.NO_CHANGE
    assert repeated.snapshot_id == first.snapshot_id
    assert len(repository.snapshots) == 1
    assert repository.verifications[-1] == (first.snapshot_id, "NO_CHANGE")
    assert repository.runs[-1].run_status == "NO_CHANGE"


async def test_same_version_with_changed_content_records_conflict_without_snapshot() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
    )

    conflict = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("source-v1"),
    )

    assert conflict.decision is SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT
    assert conflict.snapshot_id is None
    assert len(repository.snapshots) == 1
    assert repository.verifications == [(first.snapshot_id, "PASSED")]
    assert repository.runs[-1].run_status == "FAILED"
    assert repository.runs[-1].failure_code == SOURCE_VERSION_CONFLICT


async def test_a_to_b_to_a_creates_three_append_only_snapshots() -> None:
    repository = FakeSnapshotRepository()

    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("source-v1"),
    )
    second = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("source-v2"),
    )
    third = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("source-v3"),
    )

    assert [first.decision, second.decision, third.decision] == [
        SnapshotIngestionDecision.CREATED,
        SnapshotIngestionDecision.CREATED,
        SnapshotIngestionDecision.CREATED,
    ]
    assert len(repository.snapshots) == 3
    assert repository.create_requests[1].supersedes_snapshot_id == first.snapshot_id
    assert repository.create_requests[2].supersedes_snapshot_id == second.snapshot_id


async def test_changed_parser_version_creates_new_snapshot_even_when_checksum_matches() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
    )

    changed_parser = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=replace(_metadata("source-v2"), parser_version="parser-v2"),
    )

    assert changed_parser.decision is SnapshotIngestionDecision.CREATED
    assert changed_parser.snapshot_id != first.snapshot_id
    assert len(repository.snapshots) == 2


async def test_rejections_are_reflected_in_success_status() -> None:
    repository = FakeSnapshotRepository()
    metadata = replace(_metadata("source-v1"), rejected_record_count=1)

    await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=metadata,
    )

    assert repository.runs[0].run_status == "SUCCEEDED_WITH_REJECTIONS"
