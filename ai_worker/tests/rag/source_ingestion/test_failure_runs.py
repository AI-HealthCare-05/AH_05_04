from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.source_client.contracts import (
    ProviderPage,
    RetryDisposition,
    SourceClientFailure,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
)
from ai_worker.tasks.rag.source_ingestion.failure_runs import (
    FailedIngestionRunMetadata,
    IngestionProcessingFailureCode,
    record_processing_failure,
    record_source_run_failure,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotRunRecord

_OPERATION_ID = UUID("11111111-1111-4111-8111-111111111111")
_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _identity() -> SourceOperationIdentity:
    return SourceOperationIdentity("MFDS_PRODUCT_APPROVAL", "MFDS_PRODUCT_APPROVAL_API", "LIST_APPROVED_PRODUCTS")


def _metadata() -> FailedIngestionRunMetadata:
    return FailedIngestionRunMetadata(
        run_group_key="synthetic-failed-run",
        attempt_number=1,
        started_at=_NOW,
        finished_at=_NOW + timedelta(seconds=1),
        duration_ms=1000,
        reject_code_contract_version="source-reject-codes@1",
    )


def _artifact(kind: IngestionArtifactKind = IngestionArtifactKind.RAW_RESPONSE) -> StoredRawArtifact:
    is_rejection = kind is IngestionArtifactKind.REJECTS
    return StoredRawArtifact(
        page_number=None if is_rejection else 1,
        metadata=RawArtifactMetadata(
            artifact_key="reject.json" if is_rejection else "page-0001.json",
            raw_checksum="a" * 64,
            byte_size=20,
            content_type="application/json",
        ),
        storage_backend="PRIVATE_OBJECT_STORAGE",
        object_key="sha256/synthetic-artifact",
        artifact_kind=kind,
        reject_code="ITEM_SEQ_REQUIRED" if is_rejection else None,
        parser_location="page[1].record[1]" if is_rejection else None,
    )


class RecordingRepository:
    def __init__(self) -> None:
        self.locked: list[SourceOperationIdentity] = []
        self.runs: list[SnapshotRunRecord] = []
        self.artifacts: dict[UUID, tuple[StoredRawArtifact, ...]] = {}

    async def lock_operation(self, identity: SourceOperationIdentity) -> UUID:
        self.locked.append(identity)
        return _OPERATION_ID

    async def create_run(self, record: SnapshotRunRecord) -> UUID:
        self.runs.append(record)
        return uuid4()

    async def create_artifacts(
        self,
        *,
        ingestion_run_id: UUID,
        artifacts: tuple[StoredRawArtifact, ...],
    ) -> None:
        self.artifacts[ingestion_run_id] = artifacts


def _failed_source_run() -> SourceRunResult:
    return SourceRunResult(
        operation=_identity(),
        status=SourceRunStatus.FAILED,
        pages=(),
        failure=SourceClientFailure(
            code=SourceFailureCode.TIMEOUT,
            retry=RetryDisposition.BACKOFF,
            safe_message="Synthetic timeout.",
        ),
    )


async def test_records_source_failure_without_snapshot_or_partial_artifacts() -> None:
    repository = RecordingRepository()

    result = await record_source_run_failure(
        repository=repository,
        result=_failed_source_run(),
        metadata=_metadata(),
    )

    assert repository.locked == [_identity()]
    assert repository.runs == [
        SnapshotRunRecord(
            operation_id=_OPERATION_ID,
            snapshot_id=None,
            run_group_key="synthetic-failed-run",
            attempt_number=1,
            run_status="FAILED",
            started_at=_NOW,
            finished_at=_NOW + timedelta(seconds=1),
            duration_ms=1000,
            reject_code_contract_version="source-reject-codes@1",
            failure_code="TIMEOUT",
        )
    ]
    assert result.failure_code == "TIMEOUT"
    assert repository.artifacts == {}


@pytest.mark.parametrize(
    "invalid_result",
    [
        SourceRunResult(_identity(), SourceRunStatus.SUCCEEDED, (), None),
        replace(
            _failed_source_run(),
            pages=(ProviderPage(1, (), "b" * 64, "application/json"),),
        ),
    ],
)
async def test_rejects_success_or_partial_failed_source_run(invalid_result: SourceRunResult) -> None:
    repository = RecordingRepository()

    with pytest.raises(ValueError):
        await record_source_run_failure(
            repository=repository,
            result=invalid_result,
            metadata=_metadata(),
        )

    assert repository.locked == []


async def test_records_parser_failure_with_preserved_raw_artifact() -> None:
    repository = RecordingRepository()
    raw_artifact = _artifact()

    result = await record_processing_failure(
        repository=repository,
        identity=_identity(),
        metadata=_metadata(),
        failure_code=IngestionProcessingFailureCode.PARSER_VALIDATION_FAILED,
        artifacts=(raw_artifact,),
    )

    assert repository.runs[0].snapshot_id is None
    assert repository.runs[0].failure_code == "PARSER_VALIDATION_FAILED"
    assert repository.artifacts[result.ingestion_run_id] == (raw_artifact,)


async def test_rejection_limit_failure_requires_rejection_artifact() -> None:
    repository = RecordingRepository()

    with pytest.raises(ValueError, match="REJECTS Artifact"):
        await record_processing_failure(
            repository=repository,
            identity=_identity(),
            metadata=_metadata(),
            failure_code=IngestionProcessingFailureCode.REJECTION_LIMIT_EXCEEDED,
            artifacts=(_artifact(),),
        )

    assert repository.locked == []


async def test_records_rejection_limit_failure_without_snapshot() -> None:
    repository = RecordingRepository()
    artifacts = (_artifact(), _artifact(IngestionArtifactKind.REJECTS))

    result = await record_processing_failure(
        repository=repository,
        identity=_identity(),
        metadata=_metadata(),
        failure_code=IngestionProcessingFailureCode.REJECTION_LIMIT_EXCEEDED,
        artifacts=artifacts,
    )

    assert repository.runs[0].snapshot_id is None
    assert repository.runs[0].failure_code == "REJECTION_LIMIT_EXCEEDED"
    assert repository.artifacts[result.ingestion_run_id] == artifacts
