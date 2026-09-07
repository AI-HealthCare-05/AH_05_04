from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SOURCE_VERSION_CONFLICT,
    SnapshotCreateRequest,
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotReference,
    SnapshotRunRecord,
    SnapshotSelectionDecision,
    SnapshotStatusReference,
    SnapshotVerificationStatus,
    StoredRawArtifact,
    fail_snapshot_verification,
    persist_product_ingestion_result,
    select_current_snapshot,
)

_OPERATION_ID = UUID("11111111-1111-4111-8111-111111111111")
_CHECKSUM_A = "a" * 64
_CHECKSUM_B = "b" * 64
_NOW = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)


class FakeSnapshotRepository:
    def __init__(self) -> None:
        self.snapshots: list[SnapshotReference] = []
        self.create_requests: list[SnapshotCreateRequest] = []
        self.statuses: dict[UUID, SnapshotVerificationStatus] = {}
        self.verifications: list[tuple[UUID, str, str]] = []
        self.runs: list[SnapshotRunRecord] = []
        self.run_artifacts: dict[UUID, tuple[StoredRawArtifact, ...]] = {}
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
        self.statuses[snapshot_id] = SnapshotVerificationStatus.PENDING
        return snapshot_id

    async def append_verification(
        self,
        *,
        snapshot_id: UUID,
        check_name: str,
        result: str,
        verified_at: datetime,
        verified_by: str | None,
        details_summary: str | None = None,
    ) -> None:
        assert verified_at.tzinfo is not None
        _ = verified_by
        self.verifications.append((snapshot_id, check_name, result))
        _ = details_summary

    async def create_run(self, record: SnapshotRunRecord) -> UUID:
        ingestion_run_id = uuid4()
        self.runs.append(record)
        return ingestion_run_id

    async def create_artifacts(
        self,
        *,
        ingestion_run_id: UUID,
        artifacts: tuple[StoredRawArtifact, ...],
    ) -> None:
        self.run_artifacts[ingestion_run_id] = artifacts

    async def lock_snapshot_operation(self, *, snapshot_id: UUID) -> UUID:
        if snapshot_id not in self.statuses:
            raise ValueError("missing synthetic snapshot")
        return _OPERATION_ID

    async def get_snapshot_status(
        self,
        *,
        operation_id: UUID,
        snapshot_id: UUID,
    ) -> SnapshotStatusReference | None:
        assert operation_id == _OPERATION_ID
        status = self.statuses.get(snapshot_id)
        if status is None:
            return None
        return SnapshotStatusReference(snapshot_id, operation_id, status)

    async def get_current_snapshot_status(self, *, operation_id: UUID) -> SnapshotStatusReference | None:
        assert operation_id == _OPERATION_ID
        for snapshot_id, status in self.statuses.items():
            if status is SnapshotVerificationStatus.CURRENT:
                return SnapshotStatusReference(snapshot_id, operation_id, status)
        return None

    async def change_snapshot_status(
        self,
        *,
        snapshot_id: UUID,
        expected_status: SnapshotVerificationStatus,
        new_status: SnapshotVerificationStatus,
        verified_at: datetime | None = None,
        effective_at: datetime | None = None,
    ) -> bool:
        _ = verified_at, effective_at
        if self.statuses.get(snapshot_id) is not expected_status:
            return False
        self.statuses[snapshot_id] = new_status
        return True


def _stored_artifacts() -> tuple[StoredRawArtifact, ...]:
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
            object_key="source-ingestion/synthetic/page-0001.json",
        ),
    )


def _ingestion(checksum: str = _CHECKSUM_A) -> ProductIngestionResult:
    artifacts = _stored_artifacts()
    return ProductIngestionResult(
        identity=SourceOperationIdentity(
            source_code="MFDS_PRODUCT_APPROVAL",
            endpoint_code="MFDS_PRODUCT_APPROVAL_API",
            operation_code="LIST_APPROVED_PRODUCTS",
        ),
        endpoint_receipt_hash="c" * 64,
        raw_manifest_checksum=raw_manifest_checksum(artifact.metadata for artifact in artifacts),
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
        artifacts=_stored_artifacts(),
    )

    assert result.decision is SnapshotIngestionDecision.CREATED
    assert result.snapshot_id == repository.snapshots[0].snapshot_id
    assert repository.create_requests[0].supersedes_snapshot_id is None
    assert repository.verifications == [(result.snapshot_id, "source-ingestion-integrity", "PASSED")]
    assert repository.runs[0].run_status == "SUCCEEDED"
    assert repository.runs[0].snapshot_id == result.snapshot_id
    assert repository.run_artifacts[result.ingestion_run_id] == _stored_artifacts()


@pytest.mark.parametrize(
    ("ingestion", "artifacts", "message"),
    [
        (
            replace(_ingestion(), artifact_count=2),
            _stored_artifacts(),
            "Artifact 개수",
        ),
        (
            replace(_ingestion(), raw_manifest_checksum="0" * 64),
            _stored_artifacts(),
            "manifest checksum",
        ),
        (
            replace(_ingestion(), artifact_count=2),
            (_stored_artifacts()[0], _stored_artifacts()[0]),
            "page_number",
        ),
    ],
)
async def test_invalid_artifact_set_is_rejected_before_operation_lock(
    ingestion: ProductIngestionResult,
    artifacts: tuple[StoredRawArtifact, ...],
    message: str,
) -> None:
    repository = FakeSnapshotRepository()

    with pytest.raises(ValueError, match=message):
        await persist_product_ingestion_result(
            repository=repository,
            ingestion=ingestion,
            metadata=_metadata("source-v1"),
            artifacts=artifacts,
        )

    assert repository.locked_identities == []


async def test_same_content_with_new_version_appends_no_change_to_latest_snapshot() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )

    repeated = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v2"),
        artifacts=_stored_artifacts(),
    )

    assert repeated.decision is SnapshotIngestionDecision.NO_CHANGE
    assert repeated.snapshot_id == first.snapshot_id
    assert len(repository.snapshots) == 1
    assert repository.verifications[-1] == (first.snapshot_id, "source-ingestion-integrity", "NO_CHANGE")
    assert repository.runs[-1].run_status == "NO_CHANGE"


async def test_same_version_with_changed_content_records_conflict_without_snapshot() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )

    conflict = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )

    assert conflict.decision is SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT
    assert conflict.snapshot_id is None
    assert len(repository.snapshots) == 1
    assert repository.verifications == [(first.snapshot_id, "source-ingestion-integrity", "PASSED")]
    assert repository.runs[-1].run_status == "FAILED"
    assert repository.runs[-1].failure_code == SOURCE_VERSION_CONFLICT


async def test_a_to_b_to_a_creates_three_append_only_snapshots() -> None:
    repository = FakeSnapshotRepository()

    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )
    second = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("source-v2"),
        artifacts=_stored_artifacts(),
    )
    third = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("source-v3"),
        artifacts=_stored_artifacts(),
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
        artifacts=_stored_artifacts(),
    )

    changed_parser = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=replace(_metadata("source-v2"), parser_version="parser-v2"),
        artifacts=_stored_artifacts(),
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
        artifacts=_stored_artifacts(),
    )

    assert repository.runs[0].run_status == "SUCCEEDED_WITH_REJECTIONS"


async def test_selecting_new_snapshot_marks_previous_current_stale() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )
    assert first.snapshot_id is not None
    first_selection = await select_current_snapshot(
        repository=repository,
        snapshot_id=first.snapshot_id,
        selected_at=_NOW + timedelta(minutes=1),
        selected_by="synthetic-reviewer",
    )
    second = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("source-v2"),
        artifacts=_stored_artifacts(),
    )
    assert second.snapshot_id is not None

    second_selection = await select_current_snapshot(
        repository=repository,
        snapshot_id=second.snapshot_id,
        selected_at=_NOW + timedelta(minutes=2),
        selected_by="synthetic-reviewer",
    )

    assert first_selection.decision is SnapshotSelectionDecision.ACTIVATED
    assert second_selection.decision is SnapshotSelectionDecision.ACTIVATED
    assert second_selection.replaced_snapshot_id == first.snapshot_id
    assert repository.statuses[first.snapshot_id] is SnapshotVerificationStatus.STALE
    assert repository.statuses[second.snapshot_id] is SnapshotVerificationStatus.CURRENT
    assert repository.verifications[-1] == (
        second.snapshot_id,
        "snapshot-current-selection",
        "PASSED",
    )


async def test_previous_stale_snapshot_can_be_restored_without_runtime_activation() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )
    second = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("source-v2"),
        artifacts=_stored_artifacts(),
    )
    assert first.snapshot_id is not None
    assert second.snapshot_id is not None
    await select_current_snapshot(
        repository=repository,
        snapshot_id=first.snapshot_id,
        selected_at=_NOW + timedelta(minutes=1),
        selected_by=None,
    )
    await select_current_snapshot(
        repository=repository,
        snapshot_id=second.snapshot_id,
        selected_at=_NOW + timedelta(minutes=2),
        selected_by=None,
    )

    restored = await select_current_snapshot(
        repository=repository,
        snapshot_id=first.snapshot_id,
        selected_at=_NOW + timedelta(minutes=3),
        selected_by="synthetic-reviewer",
    )

    assert restored.decision is SnapshotSelectionDecision.RESTORED
    assert restored.replaced_snapshot_id == second.snapshot_id
    assert repository.statuses[first.snapshot_id] is SnapshotVerificationStatus.CURRENT
    assert repository.statuses[second.snapshot_id] is SnapshotVerificationStatus.STALE


async def test_selecting_current_snapshot_is_idempotent() -> None:
    repository = FakeSnapshotRepository()
    created = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )
    assert created.snapshot_id is not None
    await select_current_snapshot(
        repository=repository,
        snapshot_id=created.snapshot_id,
        selected_at=_NOW + timedelta(minutes=1),
        selected_by=None,
    )
    verification_count = len(repository.verifications)

    repeated = await select_current_snapshot(
        repository=repository,
        snapshot_id=created.snapshot_id,
        selected_at=_NOW + timedelta(minutes=2),
        selected_by=None,
    )

    assert repeated.decision is SnapshotSelectionDecision.ALREADY_CURRENT
    assert len(repository.verifications) == verification_count


async def test_pending_snapshot_can_fail_with_safe_code() -> None:
    repository = FakeSnapshotRepository()
    created = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )
    assert created.snapshot_id is not None

    failed = await fail_snapshot_verification(
        repository=repository,
        snapshot_id=created.snapshot_id,
        failure_code="RECORD_COUNT_MISMATCH",
        failed_at=_NOW + timedelta(minutes=1),
        verified_by="synthetic-reviewer",
    )

    assert failed.verification_status is SnapshotVerificationStatus.FAILED
    assert repository.statuses[created.snapshot_id] is SnapshotVerificationStatus.FAILED
    assert repository.verifications[-1] == (
        created.snapshot_id,
        "snapshot-verification",
        "FAILED",
    )


async def test_snapshot_failure_rejects_free_form_details() -> None:
    repository = FakeSnapshotRepository()
    created = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("source-v1"),
        artifacts=_stored_artifacts(),
    )
    assert created.snapshot_id is not None

    with pytest.raises(ValueError, match="failure_code"):
        await fail_snapshot_verification(
            repository=repository,
            snapshot_id=created.snapshot_id,
            failure_code="원문 오류 메시지",
            failed_at=_NOW + timedelta(minutes=1),
            verified_by=None,
        )

    assert repository.statuses[created.snapshot_id] is SnapshotVerificationStatus.PENDING
