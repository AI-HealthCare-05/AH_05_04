import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ai_worker.adapters.local_private_source_artifact_store import (
    LocalPrivateSourceArtifactStore,
)
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.persistence import (
    RejectionArtifactInput,
    preserve_and_persist_product_ingestion_result,
)
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SNAPSHOT_PUBLICATION_APPROVAL_CHECK,
    SOURCE_VERSION_CONFLICT,
    SnapshotCreateRequest,
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotReference,
    SnapshotRunRecord,
    SnapshotSelectionDecision,
    SnapshotStatusReference,
    SnapshotVerificationStatus,
    fail_snapshot_verification,
    persist_product_ingestion_result,
    select_current_snapshot,
)
from ai_worker.tasks.rag.source_ingestion.source_version import (
    SourceVersionValidationError,
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
        return next(
            (
                replace(item, verification_status=self.statuses[item.snapshot_id])
                for item in sorted(
                    reversed(self.snapshots),
                    key=lambda item: self.statuses[item.snapshot_id] is SnapshotVerificationStatus.FAILED,
                )
                if item.source_version == source_version
            ),
            None,
        )

    async def get_latest_snapshot(self, *, operation_id: UUID) -> SnapshotReference | None:
        assert operation_id == _OPERATION_ID
        return next(
            (
                replace(item, verification_status=self.statuses[item.snapshot_id])
                for item in reversed(self.snapshots)
                if self.statuses[item.snapshot_id] is not SnapshotVerificationStatus.FAILED
            ),
            None,
        )

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
                endpoint_receipt_hash=request.ingestion.endpoint_receipt_hash,
                rejected_record_count=request.metadata.rejected_record_count,
                verification_status=SnapshotVerificationStatus.PENDING,
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
        snapshot = next(item for item in self.snapshots if item.snapshot_id == snapshot_id)
        return SnapshotStatusReference(snapshot_id, operation_id, status, snapshot.rejected_record_count)

    async def get_current_snapshot_status(self, *, operation_id: UUID) -> SnapshotStatusReference | None:
        assert operation_id == _OPERATION_ID
        for snapshot_id, status in self.statuses.items():
            if status is SnapshotVerificationStatus.CURRENT:
                snapshot = next(item for item in self.snapshots if item.snapshot_id == snapshot_id)
                return SnapshotStatusReference(snapshot_id, operation_id, status, snapshot.rejected_record_count)
        return None

    async def has_passed_verification(
        self,
        *,
        snapshot_id: UUID,
        check_name: str,
    ) -> bool:
        return (snapshot_id, check_name, "PASSED") in self.verifications

    async def change_snapshot_status(
        self,
        *,
        snapshot_id: UUID,
        expected_status: SnapshotVerificationStatus,
        new_status: SnapshotVerificationStatus,
        verified_at: datetime | None = None,
        effective_at: datetime | None = None,
        selected_by: str | None = None,
    ) -> bool:
        _ = verified_at, effective_at
        if self.statuses.get(snapshot_id) is not expected_status:
            return False
        self.statuses[snapshot_id] = new_status
        if new_status is SnapshotVerificationStatus.CURRENT:
            self.verifications.append((snapshot_id, "snapshot-current-selection", "PASSED"))
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


def _stored_rejection_artifact() -> StoredRawArtifact:
    return StoredRawArtifact(
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
        reject_code="MISSING_ITEM_SEQ",
        parser_location="page[1].record[3]",
    )


def _metadata(source_version: str) -> SnapshotIngestionMetadata:
    external_version = source_version.removeprefix("external:") if source_version.startswith("external:") else None

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
        external_version=external_version,
        duration_ms=1000,
        verified_by="source-ingestion-worker",
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: replace(_metadata("external:v1"), source_version="x" * 256), "source_version"),
        (lambda: replace(_metadata("external:v1"), schema_version="x" * 101), "schema_version"),
        (lambda: replace(_metadata("external:v1"), parser_version="x" * 101), "parser_version"),
        (
            lambda: replace(_metadata("external:v1"), normalization_version="x" * 101),
            "normalization_version",
        ),
        (lambda: replace(_metadata("external:v1"), run_group_key="x" * 101), "run_group_key"),
        (lambda: replace(_metadata("external:v1"), verified_by="x" * 101), "verified_by"),
    ],
)
def test_rejects_snapshot_metadata_larger_than_database_contract(
    factory: Callable[[], SnapshotIngestionMetadata],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


async def test_first_result_creates_pending_snapshot_candidate_and_success_history() -> None:
    repository = FakeSnapshotRepository()

    result = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("external:v1"),
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
            metadata=_metadata("external:v1"),
            artifacts=artifacts,
        )

    assert repository.locked_identities == []


async def test_preserves_verified_original_before_snapshot_transaction(
    tmp_path: Path,
) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "page-0001.json"
    source.write_bytes(content)
    raw_metadata = RawArtifactMetadata(
        artifact_key="page-0001.json",
        raw_checksum=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/json",
    )
    ingestion = replace(
        _ingestion(),
        raw_manifest_checksum=raw_manifest_checksum((raw_metadata,)),
    )
    repository = FakeSnapshotRepository()
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")

    result = await preserve_and_persist_product_ingestion_result(
        repository=repository,
        artifact_store=store,
        ingestion=ingestion,
        metadata=_metadata("external:v1"),
        raw_artifacts=((1, source, raw_metadata),),
    )

    stored = repository.run_artifacts[result.ingestion_run_id][0]
    assert (tmp_path / "private" / stored.object_key).read_bytes() == content


async def test_manifest_mismatch_is_rejected_before_file_or_database_write(
    tmp_path: Path,
) -> None:
    content = b'{"synthetic":true}'
    source = tmp_path / "page-0001.json"
    source.write_bytes(content)
    raw_metadata = RawArtifactMetadata(
        artifact_key="page-0001.json",
        raw_checksum=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        content_type="application/json",
    )
    repository = FakeSnapshotRepository()
    storage_root = tmp_path / "private"

    with pytest.raises(ValueError, match="manifest checksum"):
        await preserve_and_persist_product_ingestion_result(
            repository=repository,
            artifact_store=LocalPrivateSourceArtifactStore(storage_root),
            ingestion=_ingestion(),
            metadata=_metadata("external:v1"),
            raw_artifacts=((1, source, raw_metadata),),
        )

    assert list(storage_root.rglob("*.artifact")) == []
    assert repository.locked_identities == []


async def test_preserves_rejection_before_success_with_rejections_run(
    tmp_path: Path,
) -> None:
    raw_content = b'{"page":1}'
    raw_path = tmp_path / "page-0001.json"
    raw_path.write_bytes(raw_content)
    raw_metadata = RawArtifactMetadata(
        artifact_key="page-0001.json",
        raw_checksum=hashlib.sha256(raw_content).hexdigest(),
        byte_size=len(raw_content),
        content_type="application/json",
    )
    rejection_content = b'{"ITEM_SEQ":null}'
    rejection_path = tmp_path / "reject-0001.json"
    rejection_path.write_bytes(rejection_content)
    rejection_metadata = RawArtifactMetadata(
        artifact_key="reject-0001.json",
        raw_checksum=hashlib.sha256(rejection_content).hexdigest(),
        byte_size=len(rejection_content),
        content_type="application/json",
    )
    ingestion = replace(
        _ingestion(),
        raw_manifest_checksum=raw_manifest_checksum((raw_metadata,)),
    )
    repository = FakeSnapshotRepository()

    result = await preserve_and_persist_product_ingestion_result(
        repository=repository,
        artifact_store=LocalPrivateSourceArtifactStore(tmp_path / "private"),
        ingestion=ingestion,
        metadata=replace(_metadata("external:v1"), rejected_record_count=1),
        raw_artifacts=((1, raw_path, raw_metadata),),
        rejection_artifacts=(
            RejectionArtifactInput(
                file_path=rejection_path,
                metadata=rejection_metadata,
                reject_code="MISSING_ITEM_SEQ",
                parser_location="page[1].record[3]",
            ),
        ),
    )

    stored = repository.run_artifacts[result.ingestion_run_id]
    assert [artifact.artifact_kind for artifact in stored] == [
        IngestionArtifactKind.RAW_RESPONSE,
        IngestionArtifactKind.REJECTS,
    ]
    assert repository.runs[0].run_status == "SUCCEEDED_WITH_REJECTIONS"


async def test_duplicate_raw_and_rejection_keys_are_rejected_before_file_write(
    tmp_path: Path,
) -> None:
    raw_content = b'{"page":1}'
    raw_path = tmp_path / "page.json"
    raw_path.write_bytes(raw_content)
    raw_metadata = RawArtifactMetadata(
        artifact_key="duplicate.json",
        raw_checksum=hashlib.sha256(raw_content).hexdigest(),
        byte_size=len(raw_content),
        content_type="application/json",
    )
    rejection_content = b'{"ITEM_SEQ":null}'
    rejection_path = tmp_path / "reject.json"
    rejection_path.write_bytes(rejection_content)
    rejection_metadata = RawArtifactMetadata(
        artifact_key="duplicate.json",
        raw_checksum=hashlib.sha256(rejection_content).hexdigest(),
        byte_size=len(rejection_content),
        content_type="application/json",
    )
    repository = FakeSnapshotRepository()
    storage_root = tmp_path / "private"

    with pytest.raises(ValueError, match="Artifact key"):
        await preserve_and_persist_product_ingestion_result(
            repository=repository,
            artifact_store=LocalPrivateSourceArtifactStore(storage_root),
            ingestion=replace(
                _ingestion(),
                raw_manifest_checksum=raw_manifest_checksum((raw_metadata,)),
            ),
            metadata=replace(_metadata("external:v1"), rejected_record_count=1),
            raw_artifacts=((1, raw_path, raw_metadata),),
            rejection_artifacts=(
                RejectionArtifactInput(
                    file_path=rejection_path,
                    metadata=rejection_metadata,
                    reject_code="MISSING_ITEM_SEQ",
                    parser_location="page[1].record[3]",
                ),
            ),
        )

    assert list(storage_root.rglob("*.artifact")) == []
    assert repository.locked_identities == []


async def test_same_content_with_new_version_appends_no_change_to_latest_snapshot() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )

    repeated = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("external:v2"),
        artifacts=_stored_artifacts(),
    )

    assert repeated.decision is SnapshotIngestionDecision.NO_CHANGE
    assert repeated.snapshot_id == first.snapshot_id
    assert len(repository.snapshots) == 1
    assert repository.verifications[-1] == (first.snapshot_id, "source-ingestion-integrity", "NO_CHANGE")
    assert repository.runs[-1].run_status == "NO_CHANGE"


async def test_changed_rejection_count_creates_new_candidate_instead_of_no_change() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )

    changed = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=replace(_metadata("external:v2"), rejected_record_count=1),
        artifacts=(*_stored_artifacts(), _stored_rejection_artifact()),
    )

    assert first.decision is SnapshotIngestionDecision.CREATED
    assert changed.decision is SnapshotIngestionDecision.CREATED
    assert changed.snapshot_id != first.snapshot_id
    assert repository.runs[-1].run_status == "SUCCEEDED_WITH_REJECTIONS"


@pytest.mark.parametrize("retry_version", ["external:v1", "external:v2"])
async def test_failed_snapshot_retry_excludes_failed_lineage(retry_version: str) -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )
    assert first.snapshot_id is not None
    await fail_snapshot_verification(
        repository=repository,
        snapshot_id=first.snapshot_id,
        failure_code="SCHEMA_DRIFT",
        failed_at=_NOW + timedelta(minutes=1),
        verified_by="synthetic-reviewer",
    )

    retry = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=replace(_metadata(retry_version), run_group_key="synthetic-retry"),
        artifacts=_stored_artifacts(),
    )

    assert retry.decision is SnapshotIngestionDecision.CREATED
    assert retry.snapshot_id != first.snapshot_id
    assert repository.create_requests[-1].supersedes_snapshot_id is None


async def test_changed_endpoint_receipt_hash_is_not_no_change() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )

    changed = await persist_product_ingestion_result(
        repository=repository,
        ingestion=replace(_ingestion(), endpoint_receipt_hash="f" * 64),
        metadata=_metadata("external:v2"),
        artifacts=_stored_artifacts(),
    )

    assert changed.decision is SnapshotIngestionDecision.CREATED
    assert changed.snapshot_id != first.snapshot_id


async def test_same_version_with_changed_content_records_conflict_without_snapshot() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )

    conflict = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("external:v1"),
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
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )
    second = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("external:v2"),
        artifacts=_stored_artifacts(),
    )
    third = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("external:v3"),
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
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )

    changed_parser = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=replace(_metadata("external:v2"), parser_version="parser-v2"),
        artifacts=_stored_artifacts(),
    )

    assert changed_parser.decision is SnapshotIngestionDecision.CREATED
    assert changed_parser.snapshot_id != first.snapshot_id
    assert len(repository.snapshots) == 2


async def test_rejections_are_reflected_in_success_status() -> None:
    repository = FakeSnapshotRepository()
    metadata = replace(_metadata("external:v1"), rejected_record_count=1)

    await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=metadata,
        artifacts=(*_stored_artifacts(), _stored_rejection_artifact()),
    )

    assert repository.runs[0].run_status == "SUCCEEDED_WITH_REJECTIONS"
    assert repository.run_artifacts[next(iter(repository.run_artifacts))][-1].artifact_kind is (
        IngestionArtifactKind.REJECTS
    )


async def test_rejected_count_requires_rejection_artifact_before_database_write() -> None:
    repository = FakeSnapshotRepository()

    with pytest.raises(ValueError, match="REJECTS Artifact"):
        await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(),
            metadata=replace(_metadata("external:v1"), rejected_record_count=1),
            artifacts=_stored_artifacts(),
        )

    assert repository.locked_identities == []


async def test_selecting_new_snapshot_marks_previous_current_stale() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("external:v1"),
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
        metadata=_metadata("external:v2"),
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


async def test_rejected_snapshot_requires_publication_approval_before_selection() -> None:
    repository = FakeSnapshotRepository()
    created = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(),
        metadata=replace(_metadata("external:v1"), rejected_record_count=1),
        artifacts=(*_stored_artifacts(), _stored_rejection_artifact()),
    )
    assert created.snapshot_id is not None

    with pytest.raises(ValueError, match="publication 승인"):
        await select_current_snapshot(
            repository=repository,
            snapshot_id=created.snapshot_id,
            selected_at=_NOW + timedelta(minutes=1),
            selected_by="synthetic-reviewer",
        )

    await repository.append_verification(
        snapshot_id=created.snapshot_id,
        check_name=SNAPSHOT_PUBLICATION_APPROVAL_CHECK,
        result="PASSED",
        verified_at=_NOW + timedelta(minutes=2),
        verified_by="synthetic-reviewer",
    )
    selected = await select_current_snapshot(
        repository=repository,
        snapshot_id=created.snapshot_id,
        selected_at=_NOW + timedelta(minutes=3),
        selected_by="synthetic-reviewer",
    )

    assert selected.decision is SnapshotSelectionDecision.ACTIVATED


async def test_previous_stale_snapshot_can_be_restored_without_runtime_activation() -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_A),
        metadata=_metadata("external:v1"),
        artifacts=_stored_artifacts(),
    )
    second = await persist_product_ingestion_result(
        repository=repository,
        ingestion=_ingestion(_CHECKSUM_B),
        metadata=_metadata("external:v2"),
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
        metadata=_metadata("external:v1"),
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
        metadata=_metadata("external:v1"),
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
        metadata=_metadata("external:v1"),
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


@pytest.mark.parametrize("changed", ["content", "receipt", "parser", "normalization", "rejections"])
async def test_failed_same_version_preserves_contract_conflict(changed: str) -> None:
    repository = FakeSnapshotRepository()
    first = await persist_product_ingestion_result(
        repository=repository, ingestion=_ingestion(), metadata=_metadata("external:v1"), artifacts=_stored_artifacts()
    )
    assert first.snapshot_id is not None
    await fail_snapshot_verification(
        repository=repository,
        snapshot_id=first.snapshot_id,
        failure_code="SCHEMA_DRIFT",
        failed_at=_NOW,
        verified_by="synthetic-reviewer",
    )
    ingestion = _ingestion(_CHECKSUM_B) if changed == "content" else _ingestion()
    metadata = _metadata("external:v1")
    artifacts = _stored_artifacts()
    if changed == "receipt":
        ingestion = replace(ingestion, endpoint_receipt_hash="f" * 64)
    elif changed == "parser":
        metadata = replace(metadata, parser_version="parser-v2")
    elif changed == "normalization":
        metadata = replace(metadata, normalization_version="normalization-v2")
    elif changed == "rejections":
        metadata = replace(metadata, rejected_record_count=1)
        artifacts = (*artifacts, _stored_rejection_artifact())
    result = await persist_product_ingestion_result(
        repository=repository,
        ingestion=ingestion,
        metadata=metadata,
        artifacts=artifacts,
    )
    assert result.decision is SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT
    assert result.snapshot_id is None
    assert len(repository.snapshots) == 1
    assert repository.runs[-1].failure_code == SOURCE_VERSION_CONFLICT


@pytest.mark.parametrize(("count", "artifact_count"), [(2, 1), (1, 2)])
async def test_reject_count_mismatch_stops_before_database(count: int, artifact_count: int) -> None:
    repository = FakeSnapshotRepository()
    with pytest.raises(ValueError, match="개수가 rejected_record_count"):
        await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(),
            metadata=replace(_metadata("external:v1"), rejected_record_count=count),
            artifacts=(*_stored_artifacts(), *(_stored_rejection_artifact(),) * artifact_count),
        )
    assert repository.locked_identities == []
    assert repository.runs == []


@pytest.mark.parametrize(("count", "artifact_count"), [(2, 1), (1, 2)])
async def test_reject_count_mismatch_stops_before_file_write(tmp_path: Path, count: int, artifact_count: int) -> None:
    repository = FakeSnapshotRepository()
    metadata = _stored_artifacts()[0].metadata
    # Input paths deliberately do not exist: cardinality must fail before reading/storing them.
    rejection = RejectionArtifactInput(
        file_path=tmp_path / "missing-reject.json",
        metadata=_stored_rejection_artifact().metadata,
        reject_code="MISSING_ITEM_SEQ",
        parser_location="$.records[0]",
    )
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    with pytest.raises(ValueError, match="개수가 rejected_record_count"):
        await preserve_and_persist_product_ingestion_result(
            repository=repository,
            artifact_store=store,
            ingestion=_ingestion(),
            metadata=replace(_metadata("external:v1"), rejected_record_count=count),
            raw_artifacts=((1, tmp_path / "missing-raw.json", metadata),),
            rejection_artifacts=(rejection,) * artifact_count,
        )
    assert list((tmp_path / "private").iterdir()) == []
    assert repository.locked_identities == []


async def test_rejects_invalid_source_version_before_locking_operation() -> None:
    repository = FakeSnapshotRepository()

    with pytest.raises(SourceVersionValidationError):
        await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(),
            metadata=replace(
                _metadata("external:v1"),
                source_version="v1",
            ),
            artifacts=_stored_artifacts(),
        )

    assert repository.locked_identities == []


async def test_rejects_external_version_mismatch_before_locking_operation() -> None:
    repository = FakeSnapshotRepository()

    with pytest.raises(SourceVersionValidationError, match="일치하지 않습니다"):
        await persist_product_ingestion_result(
            repository=repository,
            ingestion=_ingestion(),
            metadata=replace(
                _metadata("external:v1"),
                external_version="v2",
            ),
            artifacts=_stored_artifacts(),
        )

    assert repository.locked_identities == []
