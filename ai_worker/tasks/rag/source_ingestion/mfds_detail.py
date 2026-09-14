"""Full-scope MFDS detail acquisition; failed observations never become partial Snapshots."""

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx

from ai_worker.tasks.rag.catalog.mfds_component import inspect_mfds_component_rows
from ai_worker.tasks.rag.catalog.mfds_loader import DETAIL_CANONICALIZATION_SPEC
from ai_worker.tasks.rag.source_client.contracts import SourceRequest, SourceRunResult, SourceRunStatus
from ai_worker.tasks.rag.source_client.decoders import decode_mfds_json
from ai_worker.tasks.rag.source_client.endpoints import MFDS_DETAIL_CANDIDATE, MFDS_DETAIL_IDENTITY
from ai_worker.tasks.rag.source_client.mfds_client import DecodedProviderPage, MfdsSourceClient
from ai_worker.tasks.rag.source_client.security import HostResolver, resolve_host
from ai_worker.tasks.rag.source_ingestion.acquire import preserve_raw_artifacts, verify_source_run_artifacts
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    RawArtifactStore,
    read_verified_raw_artifact,
)
from ai_worker.tasks.rag.source_ingestion.failure_runs import (
    FailedIngestionRunMetadata,
    FailedIngestionRunResult,
    IngestionProcessingFailureCode,
    record_processing_failure,
    record_source_run_failure,
    record_source_version_failure,
)
from ai_worker.tasks.rag.source_ingestion.receipt_validation import (
    load_detail_endpoint_receipt,
    verify_receipt_fixture_evidence,
)
from ai_worker.tasks.rag.source_ingestion.result import SourceIngestionResult
from ai_worker.tasks.rag.source_ingestion.service import SourceAcquisitionGate
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotExclusionReceipt,
    SnapshotIngestionMetadata,
    SnapshotLifecycleRepository,
    SnapshotPersistenceResult,
    attempt_canonical_contract,
    persist_source_ingestion_result,
)
from ai_worker.tasks.rag.source_ingestion.source_version import SourceVersionValidationError

DETAIL_PARSER_VERSION = "mfds-component-acquisition@1"
DETAIL_SCOPE = "FULL_ENDPOINT"
_CONTRACT = MFDS_DETAIL_CANDIDATE.contract


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class MfdsDetailAcquisition:
    """Private raw files remain available for failure audit; no caller-selected row input."""

    result: SourceRunResult = field(repr=False)
    artifacts: tuple[tuple[int, Path, RawArtifactMetadata], ...] = field(repr=False)
    directory: Path = field(repr=False)
    started_at: datetime
    finished_at: datetime


def _write_private(path: Path, content: bytes) -> None:
    # Exclusive creation: never overwrite a prior observation or report.
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(content)


async def acquire_mfds_detail(
    *,
    gate: SourceAcquisitionGate,
    client: httpx.AsyncClient,
    secret_value: str,
    spool_parent: Path,
    resolver: HostResolver = resolve_host,
) -> MfdsDetailAcquisition:
    """Acquire every page under the caller's Operation transaction; no filters or page cutoff input.

    The private spool is retained on failure. The caller owns retention/cleanup and must
    not end the gate's transaction before persistence completes. This does not issue a Receipt.
    """
    await gate.try_lock_acquisition(MFDS_DETAIL_IDENTITY)
    started_at = datetime.now(UTC)
    directory = Path(tempfile.mkdtemp(prefix="mfds-detail-", dir=spool_parent))
    artifacts: list[tuple[int, Path, RawArtifactMetadata]] = []
    preservation_failed = False

    def decode_and_preserve(content: bytes, content_type: str) -> DecodedProviderPage:
        nonlocal preservation_failed
        decoded = decode_mfds_json(content, content_type)
        if decoded.body_code in _CONTRACT.body_codes.success_codes:
            number = decoded.page_number
            metadata = RawArtifactMetadata(
                f"detail-page-{number}.json", hashlib.sha256(content).hexdigest(), len(content), content_type
            )
            path = directory / metadata.artifact_key
            try:
                _write_private(path, content)
            except OSError:
                preservation_failed = True
                raise ValueError("MFDS detail raw preservation failed.") from None
            artifacts.append((number, path, metadata))
        return decoded

    source_client = MfdsSourceClient(
        contract=_CONTRACT,
        secret_parameter_name=MFDS_DETAIL_CANDIDATE.secret_parameter_name,
        secret_value=secret_value,
        decoder=decode_and_preserve,
        client=client,
        resolver=resolver,
    )
    # Lock acquired above, before creating spool state. It remains owned by the caller.
    result = await source_client.fetch_all_pages(SourceRequest(MFDS_DETAIL_IDENTITY, {"type": "json"}))
    if preservation_failed:
        raise RuntimeError("MFDS detail raw preservation failed; caller must roll back.")
    acquisition = MfdsDetailAcquisition(result, tuple(artifacts), directory, started_at, datetime.now(UTC))
    write_detail_report(acquisition)
    return acquisition


def _raw_rows(acquisition: MfdsDetailAcquisition) -> tuple[list[dict[str, object]], bool]:
    rows: list[dict[str, object]] = []
    totals: set[int] = set()
    numbers = []
    for number, path, metadata in acquisition.artifacts:
        decoded = decode_mfds_json(read_verified_raw_artifact(file_path=path, metadata=metadata), metadata.content_type)
        if decoded.page_number != number or decoded.body_code not in _CONTRACT.body_codes.success_codes:
            raise ValueError("MFDS detail raw page binding mismatch.")
        numbers.append(number)
        totals.add(decoded.total_count)
        rows.extend(dict(record) for record in decoded.records)
    complete = bool(rows) and numbers == list(range(1, len(numbers) + 1)) and totals == {len(rows)}
    return rows, complete


def write_detail_report(acquisition: MfdsDetailAcquisition) -> None:
    """Versioned private audit sidecar; not a DB Receipt or publication approval."""
    rows, complete = _raw_rows(acquisition)
    inspection = inspect_mfds_component_rows(tuple(rows))
    reasons: dict[str, int] = {}
    exclusions = []
    # Classify separately to retain exact source positions, including conflicting/duplicate keys.
    seen: dict[str, bytes] = {}
    for number, path, metadata in acquisition.artifacts:
        decoded = decode_mfds_json(read_verified_raw_artifact(file_path=path, metadata=metadata), metadata.content_type)
        for index, row in enumerate(decoded.records):
            single = inspect_mfds_component_rows((row,))
            reason = single.exclusions[0].reason if single.exclusions else None
            if single.observations:
                observation = single.observations[0]
                old = seen.setdefault(observation.source_record_key, observation.record_json)
                if old != observation.record_json:
                    reason = "CONFLICTING_OBSERVATION"
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                exclusions.append(
                    {
                        "page": number,
                        "record_index": index,
                        "reason": reason,
                        "artifact_checksum": metadata.raw_checksum,
                    }
                )
    canonical = _json_bytes(rows)
    payload = {
        "report_version": DETAIL_PARSER_VERSION,
        "operation": MFDS_DETAIL_IDENTITY.operation_code,
        "scope": DETAIL_SCOPE,
        "filters": {},
        "started_at": acquisition.started_at.isoformat(),
        "finished_at": acquisition.finished_at.isoformat(),
        "page_size": _CONTRACT.pagination.page_size_limit,
        "pages_observed": len(acquisition.artifacts),
        "status": acquisition.result.status.value,
        "failure_code": acquisition.result.failure.code.value if acquisition.result.failure else None,
        "pages_cover_advertised_count": complete,
        "atomic_provider_snapshot_verified": False,
        "input_count": inspection.input_count,
        "observation_count": len(inspection.observations),
        "duplicate_count": inspection.duplicate_count,
        "exclusion_count": len(inspection.exclusions),
        "exclusion_counts": reasons,
        "exclusions": exclusions,
        # 빈 주성분 행을 제외하고 Catalog를 구성한 경우 부분임을 명시한다.
        # 구성원 0개를 성분 없음·금기 없음으로 해석하지 않는다.
        "empty_component_row_count": len(inspection.empty_component_exclusions),
        "component_input_row_count": len(inspection.observations),
        "catalog_is_partial": inspection.has_excluded_empty_components,
        "catalog_input_eligible": complete
        and acquisition.result.snapshot_candidate_allowed
        and inspection.eligible_for_mapping
        and inspection.duplicate_count == 0,
        "canonical_checksum": hashlib.sha256(canonical).hexdigest() if complete else None,
        "canonicalization_spec_version": DETAIL_CANONICALIZATION_SPEC,
        "receipt_issued": False,
    }
    _write_private(acquisition.directory / "inspection.json", _json_bytes(payload))
    if complete:
        _write_private(acquisition.directory / "observations.json", canonical)


async def _record_empty_component_receipt(
    *,
    repository: SnapshotLifecycleRepository,
    snapshot_id: UUID | None,
    acquisition: MfdsDetailAcquisition,
) -> None:
    """빈 주성분 행을 제외하고 적재한 사실을 Snapshot 단위 receipt로 남깁니다.

    제외 행이 없으면 기록하지 않는다. 제외가 있는데 Snapshot이 없으면 적재가 아니므로
    남기지 않는다. 상세 원문 위치와 source record key는 private sidecar에만 둔다.
    """
    rows, _ = _raw_rows(acquisition)
    inspection = inspect_mfds_component_rows(tuple(rows))
    if snapshot_id is None or not inspection.has_excluded_empty_components:
        return
    await repository.record_observation_exclusions(
        SnapshotExclusionReceipt(
            snapshot_id=snapshot_id,
            reason="EMPTY_COMPONENT_FIELDS",
            source_row_count=inspection.input_count,
            excluded_row_count=len(inspection.empty_component_exclusions),
            retained_row_count=len(inspection.observations),
        )
    )


def build_detail_ingestion_result(
    *,
    acquisition: MfdsDetailAcquisition,
    receipt_path: Path,
    repository_root: Path,
) -> tuple[SourceIngestionResult, bytes]:
    """Re-derive the entire canonical array from raw pages; no subset argument is accepted."""
    if acquisition.result.operation != MFDS_DETAIL_IDENTITY:
        raise ValueError("MFDS detail operation mismatch.")
    evidence = load_detail_endpoint_receipt(receipt_path)
    verify_receipt_fixture_evidence(evidence=evidence.fixture_evidence, repository_root=repository_root)
    rows, complete = _raw_rows(acquisition)
    if not complete:
        raise ValueError("MFDS detail scope is incomplete.")
    verified = verify_source_run_artifacts(
        result=acquisition.result,
        artifacts=acquisition.artifacts,
        decoder=decode_mfds_json,
        success_codes=_CONTRACT.body_codes.success_codes,
    )
    inspection = inspect_mfds_component_rows(tuple(rows))
    if not inspection.eligible_for_mapping or inspection.duplicate_count:
        raise ValueError("MFDS detail observations are not eligible for a Snapshot.")
    canonical = _json_bytes(rows)
    return SourceIngestionResult(
        MFDS_DETAIL_IDENTITY,
        evidence.receipt_hash,
        verified.raw_manifest_checksum,
        hashlib.sha256(canonical).hexdigest(),
        DETAIL_CANONICALIZATION_SPEC,
        len(rows),
        len(acquisition.artifacts),
    ), canonical


async def ingest_and_persist_mfds_detail(
    *,
    acquisition: MfdsDetailAcquisition,
    repository: SnapshotLifecycleRepository,
    artifact_store: RawArtifactStore,
    metadata: SnapshotIngestionMetadata,
    receipt_path: Path,
    repository_root: Path,
) -> SnapshotPersistenceResult | FailedIngestionRunResult:
    """Caller owns commit/rollback. Successful acquisition still requires endpoint evidence.

    Blank/invalid/duplicate observations preserve raw evidence and a FAILED run, not a
    partial Snapshot. The report stays in the private acquisition bundle for retention.
    """
    if (
        acquisition.result.operation != MFDS_DETAIL_IDENTITY
        or metadata.parser_version != DETAIL_PARSER_VERSION
        or metadata.rejected_record_count != 0
        or metadata.reject_code_contract_version is not None
    ):
        raise ValueError("MFDS detail ingestion contract mismatch.")
    failed_metadata = FailedIngestionRunMetadata(
        metadata.run_group_key, metadata.attempt_number, metadata.started_at, metadata.finished_at, metadata.duration_ms
    )
    try:
        stored = (
            preserve_raw_artifacts(artifacts=acquisition.artifacts, store=artifact_store)
            if acquisition.artifacts
            else ()
        )
    except Exception:
        raise RuntimeError("MFDS detail raw preservation failed; caller must roll back.") from None
    if acquisition.result.status is not SourceRunStatus.SUCCEEDED:
        failure = await record_source_run_failure(
            repository=repository, result=acquisition.result, metadata=failed_metadata
        )
        if stored:
            await repository.create_artifacts(ingestion_run_id=failure.ingestion_run_id, artifacts=stored)
        return failure
    try:
        ingestion, _ = build_detail_ingestion_result(
            acquisition=acquisition, receipt_path=receipt_path, repository_root=repository_root
        )
    except ValueError:
        return await record_processing_failure(
            repository=repository,
            identity=MFDS_DETAIL_IDENTITY,
            metadata=failed_metadata,
            failure_code=IngestionProcessingFailureCode.PARSER_VALIDATION_FAILED,
            artifacts=stored,
        )
    try:
        persisted = await persist_source_ingestion_result(
            repository=repository,
            ingestion=ingestion,
            metadata=metadata,
            artifacts=stored,
        )
        await _record_empty_component_receipt(
            repository=repository, snapshot_id=persisted.snapshot_id, acquisition=acquisition
        )
        return persisted
    except SourceVersionValidationError:
        failure = await record_source_version_failure(
            repository=repository,
            identity=MFDS_DETAIL_IDENTITY,
            metadata=failed_metadata,
            source_version=metadata.source_version,
            external_version=metadata.external_version,
            canonical_contract=attempt_canonical_contract(ingestion=ingestion, metadata=metadata),
        )
        await repository.create_artifacts(ingestion_run_id=failure.ingestion_run_id, artifacts=stored)
        return failure
