"""검증된 Source 원본 보관과 Snapshot 저장을 순서대로 연결합니다."""

import hashlib
import json
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from ai_worker.tasks.rag.source_client.contracts import (
    ProviderPage,
    SourceFailureCode,
    SourceOperationIdentity,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.decoders import decode_mfds_json
from ai_worker.tasks.rag.source_client.endpoints import MFDS_ENDPOINT_CANDIDATES
from ai_worker.tasks.rag.source_ingestion.acquire import (
    preserve_raw_artifacts,
    preserve_rejection_artifact,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    RawArtifactStore,
    StoredRawArtifact,
    read_verified_raw_artifact,
    validate_artifact_binding,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.failure_runs import (
    FailedIngestionRunMetadata,
    FailedIngestionRunResult,
    IngestionProcessingFailureCode,
    record_processing_failure,
    record_source_run_failure,
    record_source_version_failure,
)
from ai_worker.tasks.rag.source_ingestion.product_rejections import ProductIdentityError, ProductRejection
from ai_worker.tasks.rag.source_ingestion.reject_codes import (
    PRODUCT_REJECT_IDENTITY,
    REJECT_CODE_CONTRACT_VERSION,
    RejectContractError,
    parser_location_identity,
    validate_parser_contract,
    validate_reject_artifact,
)
from ai_worker.tasks.rag.source_ingestion.result import ProductIngestionResult, build_product_ingestion_result
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotLifecycleRepository,
    SnapshotPersistenceResult,
    attempt_canonical_contract,
    persist_product_ingestion_result,
)
from ai_worker.tasks.rag.source_ingestion.source_version import (
    SourceVersionValidationError,
    validate_source_version,
)


@dataclass(frozen=True, slots=True)
class RejectionArtifactInput:
    file_path: Path
    metadata: RawArtifactMetadata
    reject_code: str
    parser_location: str

    def __post_init__(self) -> None:
        validate_artifact_binding(
            page_number=None,
            artifact_kind=IngestionArtifactKind.REJECTS,
            reject_code=self.reject_code,
            parser_location=self.parser_location,
        )


def _validate_rejection_inputs(
    ingestion: ProductIngestionResult,
    metadata: SnapshotIngestionMetadata,
    rejection_entries: tuple[RejectionArtifactInput, ...],
) -> None:
    if rejection_entries and len(rejection_entries) != metadata.rejected_record_count:
        raise ValueError("REJECTS Artifact 개수가 rejected_record_count와 일치하지 않습니다.")
    if metadata.reject_code_contract_version is not None:
        validate_parser_contract(
            identity=ingestion.identity,
            version=metadata.reject_code_contract_version,
            parser_version=metadata.parser_version,
        )
    for entry in rejection_entries:
        validate_reject_artifact(
            identity=ingestion.identity,
            version=metadata.reject_code_contract_version,
            code=entry.reject_code,
            location=entry.parser_location,
        )
    locations = [parser_location_identity(entry.parser_location) for entry in rejection_entries]
    if len(locations) != len(set(locations)):
        raise RejectContractError()


async def preserve_and_persist_product_ingestion_result(
    *,
    repository: SnapshotLifecycleRepository,
    artifact_store: RawArtifactStore,
    ingestion: ProductIngestionResult,
    metadata: SnapshotIngestionMetadata,
    raw_artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
    rejection_artifacts: Iterable[RejectionArtifactInput] = (),
) -> SnapshotPersistenceResult:
    """검증 결과와 같은 원본만 불변 보관한 뒤 DB transaction에 연결합니다."""
    rejection_entries = tuple(rejection_artifacts)
    try:
        _validate_rejection_inputs(ingestion, metadata, rejection_entries)
    except RejectContractError:
        return await _record_parser_failure(
            repository=repository, identity=ingestion.identity, metadata=metadata, artifacts=()
        )
    try:
        validate_source_version(
            source_version=metadata.source_version,
            external_version=metadata.external_version,
            canonical_checksum=ingestion.canonical_checksum,
        )
    except SourceVersionValidationError:
        failed = await record_source_version_failure(
            repository=repository,
            identity=ingestion.identity,
            metadata=FailedIngestionRunMetadata(
                run_group_key=metadata.run_group_key,
                attempt_number=metadata.attempt_number,
                started_at=metadata.started_at,
                finished_at=metadata.finished_at,
                duration_ms=metadata.duration_ms,
                reject_code_contract_version=metadata.reject_code_contract_version,
            ),
            source_version=metadata.source_version,
            external_version=metadata.external_version,
            canonical_contract=attempt_canonical_contract(ingestion=ingestion, metadata=metadata),
        )
        return SnapshotPersistenceResult(
            SnapshotIngestionDecision.VALIDATION_FAILED,
            failed.operation_id,
            failed.ingestion_run_id,
            None,
            failed.failure_code,
        )
    entries = tuple(raw_artifacts)
    if len(entries) != ingestion.artifact_count:
        raise ValueError("Artifact 개수가 검증된 수집 결과와 일치하지 않습니다.")
    page_numbers = {page_number for page_number, _, _ in entries}
    if len(page_numbers) != len(entries):
        raise ValueError("Artifact page_number는 수집 실행 안에서 중복될 수 없습니다.")
    manifest_checksum = raw_manifest_checksum(metadata for _, _, metadata in entries)
    if manifest_checksum != ingestion.raw_manifest_checksum:
        raise ValueError("Artifact manifest checksum이 검증된 수집 결과와 일치하지 않습니다.")
    if metadata.rejected_record_count == 0 and rejection_entries:
        raise ValueError("거부 레코드가 없는 실행에는 REJECTS Artifact를 기록할 수 없습니다.")
    if metadata.rejected_record_count > 0 and not rejection_entries:
        raise ValueError("거부 레코드가 있는 실행에는 REJECTS Artifact가 필요합니다.")
    if len(rejection_entries) != metadata.rejected_record_count:
        raise ValueError("REJECTS Artifact 개수가 rejected_record_count와 일치하지 않습니다.")
    artifact_keys = [entry_metadata.artifact_key for _, _, entry_metadata in entries]
    artifact_keys.extend(entry.metadata.artifact_key for entry in rejection_entries)
    if len(set(artifact_keys)) != len(artifact_keys):
        raise ValueError("Artifact key는 수집 실행 안에서 중복될 수 없습니다.")

    stored_artifacts = preserve_raw_artifacts(
        artifacts=entries,
        store=artifact_store,
    )
    stored_rejections = tuple(
        preserve_rejection_artifact(
            file_path=entry.file_path,
            metadata=entry.metadata,
            reject_code=entry.reject_code,
            parser_location=entry.parser_location,
            store=artifact_store,
            identity=ingestion.identity,
            reject_code_contract_version=metadata.reject_code_contract_version,
        )
        for entry in rejection_entries
    )
    return await persist_product_ingestion_result(
        repository=repository,
        ingestion=ingestion,
        metadata=metadata,
        artifacts=(*stored_artifacts, *stored_rejections),
    )


def _failure_metadata(
    metadata: SnapshotIngestionMetadata, identity: SourceOperationIdentity
) -> FailedIngestionRunMetadata:
    # Never persist arbitrary unsupported version text or incorrectly label its contract.
    version = metadata.reject_code_contract_version
    if identity != PRODUCT_REJECT_IDENTITY or version != REJECT_CODE_CONTRACT_VERSION:
        version = None
    return FailedIngestionRunMetadata(
        metadata.run_group_key,
        metadata.attempt_number,
        metadata.started_at,
        metadata.finished_at,
        metadata.duration_ms,
        version,
    )


async def _record_parser_failure(
    *,
    repository: SnapshotLifecycleRepository,
    identity: SourceOperationIdentity,
    metadata: SnapshotIngestionMetadata,
    artifacts: tuple[StoredRawArtifact, ...],
) -> SnapshotPersistenceResult:
    failed = await record_processing_failure(
        repository=repository,
        identity=identity,
        metadata=_failure_metadata(metadata, identity),
        failure_code=IngestionProcessingFailureCode.PARSER_VALIDATION_FAILED,
        artifacts=artifacts,
    )
    return SnapshotPersistenceResult(
        SnapshotIngestionDecision.VALIDATION_FAILED,
        failed.operation_id,
        failed.ingestion_run_id,
        None,
        failed.failure_code,
    )


def _preserve_product_rejections(
    *,
    result: SourceRunResult,
    rejections: tuple[ProductRejection, ...],
    artifact_store: RawArtifactStore,
    version: str | None,
) -> tuple[StoredRawArtifact, ...]:
    # Pages were matched against verified raw bytes by build_product_ingestion_result.
    pages = {page.page_number: page.records for page in result.pages}
    stored = []
    for rejection in rejections:
        validate_reject_artifact(
            identity=result.operation, version=version, code=rejection.code, location=rejection.parser_location
        )
        content = json.dumps(
            dict(pages[rejection.page_number][rejection.record_index]),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        checksum = hashlib.sha256(content).hexdigest()
        metadata = RawArtifactMetadata(
            f"reject-{rejection.page_number}-{rejection.record_index}.json", checksum, len(content), "application/json"
        )
        # NamedTemporaryFile uses private permissions; the source record never enters diagnostics.
        with tempfile.NamedTemporaryFile(prefix="source-reject-", suffix=".json") as temporary:
            temporary.write(content)
            temporary.flush()
            stored.append(
                preserve_rejection_artifact(
                    file_path=Path(temporary.name),
                    metadata=metadata,
                    reject_code=rejection.code,
                    parser_location=rejection.parser_location,
                    store=artifact_store,
                    identity=result.operation,
                    reject_code_contract_version=version,
                )
            )
    return tuple(stored)


async def ingest_and_persist_product_run(
    *,
    repository: SnapshotLifecycleRepository,
    artifact_store: RawArtifactStore,
    result: SourceRunResult,
    metadata: SnapshotIngestionMetadata,
    raw_artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
    receipt_path: Path,
    repository_root: Path,
) -> SnapshotPersistenceResult | FailedIngestionRunResult:
    """Actual raw-run → parser → safe failure or Snapshot boundary. Caller owns transaction.

    No commit is hidden here. On storage/DB error the caller must roll back; content-addressed
    objects remain eligible for the existing #347 orphan reconciliation, never inline deletion.
    """
    try:
        validate_parser_contract(
            identity=result.operation,
            version=metadata.reject_code_contract_version,
            parser_version=metadata.parser_version,
        )
    except RejectContractError:
        return await _record_parser_failure(
            repository=repository, identity=result.operation, metadata=metadata, artifacts=()
        )
    entries = tuple(raw_artifacts)
    if _is_complete_identity_failure(result):
        try:
            result = _recover_complete_product_pages(result, entries)
        except (TypeError, ValueError):
            return await _record_parser_failure(
                repository=repository, identity=result.operation, metadata=metadata, artifacts=()
            )
    if result.status is not SourceRunStatus.SUCCEEDED:
        return await record_source_run_failure(
            repository=repository, result=result, metadata=_failure_metadata(metadata, result.operation)
        )
    try:
        ingestion = build_product_ingestion_result(
            result=result, artifacts=entries, receipt_path=receipt_path, repository_root=repository_root
        )
    except ProductIdentityError as error:
        rejects = error.rejections
    except ValueError:
        return await _record_parser_failure(
            repository=repository, identity=result.operation, metadata=metadata, artifacts=()
        )
    else:
        return await preserve_and_persist_product_ingestion_result(
            repository=repository,
            artifact_store=artifact_store,
            ingestion=ingestion,
            metadata=replace(metadata, rejected_record_count=0),
            raw_artifacts=entries,
        )
    try:
        stored_raw = preserve_raw_artifacts(artifacts=entries, store=artifact_store)
        stored_rejects = _preserve_product_rejections(
            result=result,
            rejections=rejects,
            artifact_store=artifact_store,
            version=metadata.reject_code_contract_version,
        )
    except Exception:
        # Adapter/temporary-file exceptions can contain provider values or paths.
        # Preserve cancellation (BaseException) and require the caller's rollback.
        raise RuntimeError("Source Artifact preservation failed.") from None
    return await _record_parser_failure(
        repository=repository, identity=result.operation, metadata=metadata, artifacts=(*stored_raw, *stored_rejects)
    )


def _is_complete_identity_failure(result: SourceRunResult) -> bool:
    return (
        result.operation == PRODUCT_REJECT_IDENTITY
        and result.status is SourceRunStatus.SCHEMA_DRIFT
        and result.failure is not None
        and result.failure.code is SourceFailureCode.SCHEMA_DRIFT
        and result.primary_key_validation is not None
        and not result.primary_key_validation.passed
        and not result.pages
    )


def _recover_complete_product_pages(
    result: SourceRunResult,
    entries: tuple[tuple[int, Path, RawArtifactMetadata], ...],
) -> SourceRunResult:
    """Recover complete PK-failure artifacts for rejection auditing, never an eligible Snapshot."""
    pages = []
    if not entries or len({number for number, _, _ in entries}) != len(entries):
        raise ValueError("Product failure artifact set is invalid.")
    for number, path, metadata in sorted(entries, key=lambda item: item[0]):
        content = read_verified_raw_artifact(file_path=path, metadata=metadata)
        decoded = decode_mfds_json(content, metadata.content_type)
        success_codes = MFDS_ENDPOINT_CANDIDATES["LIST_APPROVED_PRODUCTS"].contract.body_codes.success_codes
        if decoded.body_code not in success_codes or decoded.page_number != number:
            raise ValueError("Product failure artifact binding is invalid.")
        pages.append(
            ProviderPage(number, decoded.records, metadata.raw_checksum, metadata.content_type, decoded.total_count)
        )
    # Primary-key evidence stays failed. build_product_ingestion_result requires actual
    # identity validation before checksum and rejects even clean data with failed evidence.
    return replace(result, status=SourceRunStatus.SUCCEEDED, failure=None, pages=tuple(pages), full_scan_completed=True)
