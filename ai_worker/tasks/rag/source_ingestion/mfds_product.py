"""Full-scope MFDS product approval acquisition; the actual LIST_APPROVED_PRODUCTS data plane.

이 모듈은 새 parser·canonicalization을 만들지 않는다. 수집한 원본 페이지를 그대로 보존한 뒤
기존 제품 Parser(`ingest_and_persist_product_run`)에 넘기는 실행 경로만 제공한다. Endpoint
Receipt는 호출자가 명시적으로 지정하며, 저장소에 보관된 과거 Receipt가 기본값이 되지 않는다.
"""

import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ai_worker.tasks.rag.source_client.contracts import SourceRequest, SourceRunResult, SourceRunStatus
from ai_worker.tasks.rag.source_client.decoders import decode_mfds_json
from ai_worker.tasks.rag.source_client.endpoints import MFDS_ENDPOINT_CANDIDATES
from ai_worker.tasks.rag.source_client.mfds_client import DecodedProviderPage, MfdsSourceClient
from ai_worker.tasks.rag.source_client.security import HostResolver, resolve_host
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    RawArtifactStore,
    read_verified_raw_artifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import product_canonical_checksum, raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.failure_runs import FailedIngestionRunResult
from ai_worker.tasks.rag.source_ingestion.parse import PRODUCT_CANONICALIZATION_SPEC_VERSION
from ai_worker.tasks.rag.source_ingestion.persistence import ingest_and_persist_product_run
from ai_worker.tasks.rag.source_ingestion.receipt_validation import (
    EndpointReceiptEvidence,
    load_product_endpoint_receipt,
    verify_receipt_fixture_evidence,
)
from ai_worker.tasks.rag.source_ingestion.reject_codes import (
    PRODUCT_REJECT_PARSER_VERSION,
    REJECT_CODE_CONTRACT_VERSION,
)
from ai_worker.tasks.rag.source_ingestion.service import SourceAcquisitionGate
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionMetadata,
    SnapshotLifecycleRepository,
    SnapshotPersistenceResult,
)
from ai_worker.tasks.rag.source_ingestion.source_version import build_api_source_version

PRODUCT_CANDIDATE = MFDS_ENDPOINT_CANDIDATES["LIST_APPROVED_PRODUCTS"]
_CONTRACT = PRODUCT_CANDIDATE.contract
PRODUCT_IDENTITY = _CONTRACT.identity
PRODUCT_SCOPE = "FULL_ENDPOINT"
PRODUCT_SCHEMA_VERSION = "mfds-product-response@1"
# normalization_version은 canonicalization spec과 의미가 다르다. 후자는 checksum을 만드는
# 직렬화 규격(PRODUCT_CANONICALIZATION_SPEC_VERSION)이고, 이 값은 Snapshot provenance가
# 기록하는 정규화 단계 식별자다. mfds_label의 NORMALIZATION_VERSION과 같은 어휘를 쓴다.
PRODUCT_NORMALIZATION_VERSION = "mfds-product-normalization@1"
_REPORT_VERSION = "mfds-product-acquisition@1"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write_private(path: Path, content: bytes) -> None:
    # Exclusive creation: never overwrite a prior observation or report.
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(content)


@dataclass(frozen=True, slots=True)
class MfdsProductAcquisition:
    """Private raw pages kept for audit; no caller-selected ITEM_SEQ or page filter."""

    result: SourceRunResult = field(repr=False)
    artifacts: tuple[tuple[int, Path, RawArtifactMetadata], ...] = field(repr=False)
    directory: Path = field(repr=False)
    started_at: datetime
    finished_at: datetime


async def acquire_mfds_product(
    *,
    gate: SourceAcquisitionGate,
    client: httpx.AsyncClient,
    secret_value: str,
    spool_parent: Path,
    resolver: HostResolver = resolve_host,
) -> MfdsProductAcquisition:
    """Acquire every page under the caller's Operation transaction; no filters or page cutoff.

    수집은 호출자 transaction의 Source acquisition lock 아래에서만 실행된다. 실패해도 private
    spool은 남기며, 보존·정리는 호출자 책임이다. 이 함수는 Receipt를 발급하지 않는다.
    """
    await gate.try_lock_acquisition(PRODUCT_IDENTITY)
    started_at = datetime.now(UTC)
    directory = Path(tempfile.mkdtemp(prefix="mfds-product-", dir=spool_parent))
    artifacts: list[tuple[int, Path, RawArtifactMetadata]] = []
    preservation_failed = False

    def decode_and_preserve(content: bytes, content_type: str) -> DecodedProviderPage:
        nonlocal preservation_failed
        decoded = decode_mfds_json(content, content_type)
        if decoded.body_code in _CONTRACT.body_codes.success_codes:
            number = decoded.page_number
            metadata = RawArtifactMetadata(
                f"product-page-{number}.json", hashlib.sha256(content).hexdigest(), len(content), content_type
            )
            path = directory / metadata.artifact_key
            try:
                _write_private(path, content)
            except OSError:
                preservation_failed = True
                raise ValueError("MFDS product raw preservation failed.") from None
            artifacts.append((number, path, metadata))
        return decoded

    source_client = MfdsSourceClient(
        contract=_CONTRACT,
        secret_parameter_name=PRODUCT_CANDIDATE.secret_parameter_name,
        secret_value=secret_value,
        decoder=decode_and_preserve,
        client=client,
        resolver=resolver,
    )
    request = SourceRequest(PRODUCT_IDENTITY, dict(PRODUCT_CANDIDATE.request_parameters))
    result = await source_client.fetch_all_pages(request)
    if preservation_failed:
        raise RuntimeError("MFDS product raw preservation failed; caller must roll back.")
    return MfdsProductAcquisition(result, tuple(artifacts), directory, started_at, datetime.now(UTC))


def _decoded_pages(acquisition: MfdsProductAcquisition) -> tuple[tuple[int, DecodedProviderPage], ...]:
    """Re-decode preserved bytes; a page that no longer binds to its artifact is rejected."""
    pages = []
    for number, path, metadata in sorted(acquisition.artifacts, key=lambda item: item[0]):
        decoded = decode_mfds_json(read_verified_raw_artifact(file_path=path, metadata=metadata), metadata.content_type)
        if decoded.page_number != number or decoded.body_code not in _CONTRACT.body_codes.success_codes:
            raise ValueError("MFDS product raw page binding mismatch.")
        pages.append((number, decoded))
    return tuple(pages)


def _observed_records(acquisition: MfdsProductAcquisition) -> tuple[tuple[Mapping[str, object], ...], bool]:
    """Return every preserved record and whether the pages cover the advertised total."""
    numbers: list[int] = []
    totals: set[int] = set()
    records: list[Mapping[str, object]] = []
    for number, decoded in _decoded_pages(acquisition):
        numbers.append(number)
        totals.add(decoded.total_count)
        records.extend(decoded.records)
    complete = bool(records) and numbers == list(range(1, len(numbers) + 1)) and totals == {len(records)}
    return tuple(records), complete


def observed_canonical_checksum(acquisition: MfdsProductAcquisition) -> str | None:
    """Canonical checksum of the preserved run, or None when ITEM_SEQ identity is not valid.

    None은 기존 제품 Parser가 거부할 실행을 뜻한다. 여기서 거부 사유를 다시 판정하지 않으며,
    위치가 담긴 판정은 `build_product_ingestion_result`가 실제 page 번호로 수행한다.
    """
    records, complete = _observed_records(acquisition)
    if not complete:
        return None
    try:
        return product_canonical_checksum(records)
    except ValueError:
        return None


def write_product_report(
    acquisition: MfdsProductAcquisition,
    *,
    receipt: EndpointReceiptEvidence | None = None,
) -> Path:
    """Versioned private audit sidecar; not a DB Receipt or a publication approval.

    관측값만 기록한다. 특정 record/page 수를 영구 invariant로 두지 않고, 비교 대상이 필요하면
    호출자가 지정한 Endpoint Receipt의 `validated_record_count`만 함께 남긴다.
    """
    records, complete = _observed_records(acquisition)
    item_sequences = [record.get("ITEM_SEQ") for record in records]
    present = [value for value in item_sequences if isinstance(value, str) and value.strip()]
    serialized = [_json_bytes(dict(record)) for record in records]
    canonical_checksum = observed_canonical_checksum(acquisition)
    receipt_count = receipt.validated_record_count if receipt is not None else None
    payload = {
        "report_version": _REPORT_VERSION,
        "operation": PRODUCT_IDENTITY.operation_code,
        "scope": PRODUCT_SCOPE,
        "filters": {},
        "started_at": acquisition.started_at.isoformat(),
        "finished_at": acquisition.finished_at.isoformat(),
        "page_size": _CONTRACT.pagination.page_size_limit,
        "pages_observed": len(acquisition.artifacts),
        "status": acquisition.result.status.value,
        "failure_code": acquisition.result.failure.code.value if acquisition.result.failure else None,
        "pages_cover_advertised_count": complete,
        "atomic_provider_snapshot_verified": False,
        "record_count": len(records),
        "primary_key_null_count": len(records) - len(present),
        "primary_key_duplicate_count": len(present) - len(set(present)),
        "whole_record_duplicate_count": len(serialized) - len(set(serialized)),
        "canonical_checksum": canonical_checksum,
        "canonicalization_spec_version": PRODUCT_CANONICALIZATION_SPEC_VERSION,
        "raw_manifest_checksum": raw_manifest_checksum(metadata for _, _, metadata in acquisition.artifacts)
        if acquisition.artifacts
        else None,
        # 비교 기준은 호출자가 넘긴 Receipt에서만 온다. 저장소의 과거 Receipt를 기본값으로 쓰지 않는다.
        "receipt_validated_record_count": receipt_count,
        "matches_receipt_record_count": None if receipt_count is None else receipt_count == len(records),
        "snapshot_candidate_allowed": acquisition.result.snapshot_candidate_allowed,
        "receipt_issued": False,
    }
    path = acquisition.directory / "inspection.json"
    _write_private(path, _json_bytes(payload))
    return path


def load_product_receipt(*, receipt_path: Path, repository_root: Path) -> EndpointReceiptEvidence:
    """Load the caller-supplied Endpoint Receipt and verify its fixture evidence."""
    evidence = load_product_endpoint_receipt(receipt_path)
    verify_receipt_fixture_evidence(evidence=evidence.fixture_evidence, repository_root=repository_root)
    return evidence


def build_product_snapshot_metadata(
    *,
    acquisition: MfdsProductAcquisition,
    run_group_key: str,
    verified_by: str | None = None,
    attempt_number: int = 1,
) -> SnapshotIngestionMetadata:
    """Bind the run's own observed checksum to an API source_version; no external version exists.

    canonical checksum을 만들 수 없는 실행은 제품 Parser가 Snapshot 없이 거부한다. 그때는
    같은 실행의 raw manifest checksum을 payload로 둔다. 이 값은 실패 Run 기록에만 남고
    Snapshot source_version으로 검증되는 경로에 도달하지 않는다.
    """
    checksum = observed_canonical_checksum(acquisition)
    if checksum is None:
        checksum = raw_manifest_checksum(metadata for _, _, metadata in acquisition.artifacts)
    collected_at = acquisition.finished_at
    duration_ms = max(0, int((acquisition.finished_at - acquisition.started_at).total_seconds() * 1000))
    return SnapshotIngestionMetadata(
        source_version=build_api_source_version(collected_at=collected_at, canonical_checksum=checksum),
        schema_version=PRODUCT_SCHEMA_VERSION,
        parser_version=PRODUCT_REJECT_PARSER_VERSION,
        normalization_version=PRODUCT_NORMALIZATION_VERSION,
        rejected_record_count=0,
        run_group_key=run_group_key,
        attempt_number=attempt_number,
        started_at=acquisition.started_at,
        finished_at=acquisition.finished_at,
        collected_at=collected_at,
        external_version=None,
        duration_ms=duration_ms,
        verified_by=verified_by,
        reject_code_contract_version=REJECT_CODE_CONTRACT_VERSION,
    )


def require_receipt_matches_observed_run(
    *,
    acquisition: MfdsProductAcquisition,
    receipt_path: Path,
    repository_root: Path,
) -> None:
    """Refuse stale Endpoint evidence before any Snapshot can be created.

    전수 수집이 끝난 실행에 한해 Receipt의 validated_record_count와 관측 record_count가
    정확히 같아야 한다. 과거 건수로 발급된 Receipt가 현재 실행의 증빙이 되면 안 되기
    때문이다. 비교 기준은 호출자가 지정한 Receipt에서만 오며 어떤 건수도 코드에 고정하지
    않는다. 수집 자체가 실패한 실행은 Snapshot에 도달할 수 없으므로 여기서 막지 않는다.
    기존 실패 Run 기록 경로를 유지해 감사 증적을 잃지 않는다.
    """
    if acquisition.result.status is not SourceRunStatus.SUCCEEDED:
        return
    receipt = load_product_receipt(receipt_path=receipt_path, repository_root=repository_root)
    if receipt.validated_record_count != acquisition.result.record_count:
        # 관측값과 Receipt 건수를 메시지에 담지 않는다. 대조는 private sidecar 보고서로 한다.
        raise ValueError("MFDS product Endpoint Receipt record count does not match the observed run.")


async def ingest_and_persist_mfds_product(
    *,
    acquisition: MfdsProductAcquisition,
    repository: SnapshotLifecycleRepository,
    artifact_store: RawArtifactStore,
    metadata: SnapshotIngestionMetadata,
    receipt_path: Path,
    repository_root: Path,
) -> SnapshotPersistenceResult | FailedIngestionRunResult:
    """Hand the preserved run to the existing product Parser boundary. Caller owns the transaction.

    Receipt 경로는 호출자가 반드시 지정한다. 여기서 저장소의 기존 Receipt로 되돌아가지 않는다.
    """
    if acquisition.result.operation != PRODUCT_IDENTITY:
        raise ValueError("MFDS product operation mismatch.")
    if (
        metadata.parser_version != PRODUCT_REJECT_PARSER_VERSION
        or metadata.reject_code_contract_version != REJECT_CODE_CONTRACT_VERSION
        or metadata.rejected_record_count != 0
        or metadata.external_version is not None
    ):
        raise ValueError("MFDS product ingestion contract mismatch.")
    require_receipt_matches_observed_run(
        acquisition=acquisition, receipt_path=receipt_path, repository_root=repository_root
    )
    return await ingest_and_persist_product_run(
        repository=repository,
        artifact_store=artifact_store,
        result=acquisition.result,
        metadata=metadata,
        raw_artifacts=acquisition.artifacts,
        receipt_path=receipt_path,
        repository_root=repository_root,
    )
