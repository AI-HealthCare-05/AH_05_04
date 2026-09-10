"""수집한 원본 Artifact 목록의 무결성을 검증합니다."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_worker.tasks.rag.source_client.contracts import (
    ProviderPage,
    SourceOperationIdentity,
    SourceRunResult,
)
from ai_worker.tasks.rag.source_client.mfds_client import ResponseDecoder
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    RawArtifactStore,
    StoredRawArtifact,
    read_verified_raw_artifact,
    verify_raw_artifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes
from ai_worker.tasks.rag.source_ingestion.reject_codes import (
    PRODUCT_REJECT_IDENTITY,
    REJECT_CODE_CONTRACT_VERSION,
    validate_reject_artifact,
)
from ai_worker.tasks.rag.source_ingestion.validation import (
    require_complete_source_run,
)


@dataclass(frozen=True, slots=True)
class VerifiedSourceRunArtifacts:
    """검증한 원본과 그 원본에서 직접 해석한 레코드입니다."""

    raw_manifest_checksum: str
    records: tuple[Mapping[str, object], ...]


def preserve_raw_artifacts(
    *,
    artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
    store: RawArtifactStore,
) -> tuple[StoredRawArtifact, ...]:
    """페이지 결속을 확인한 원본을 검증하며 불변 저장소에 보존합니다."""
    artifacts_by_page = _index_raw_artifacts(artifacts)
    raw_manifest_checksum(metadata for _, metadata in artifacts_by_page.values())

    return tuple(
        store.put_verified(
            page_number=page_number,
            file_path=file_path,
            metadata=metadata,
        )
        for page_number, (file_path, metadata) in sorted(artifacts_by_page.items())
    )


def preserve_rejection_artifact(
    *,
    file_path: Path,
    metadata: RawArtifactMetadata,
    reject_code: str,
    parser_location: str,
    store: RawArtifactStore,
    identity: SourceOperationIdentity = PRODUCT_REJECT_IDENTITY,
    reject_code_contract_version: str | None = REJECT_CODE_CONTRACT_VERSION,
) -> StoredRawArtifact:
    """거부 원문을 안전한 코드·Parser 위치와 함께 불변 보존합니다."""
    validate_reject_artifact(
        identity=identity, version=reject_code_contract_version, code=reject_code, location=parser_location
    )
    return store.put_verified(
        page_number=None,
        file_path=file_path,
        metadata=metadata,
        artifact_kind=IngestionArtifactKind.REJECTS,
        reject_code=reject_code,
        parser_location=parser_location,
    )


def verify_raw_artifact_manifest(
    artifacts: Iterable[tuple[Path, RawArtifactMetadata]],
) -> str:
    """전달된 모든 파일이 검증된 경우에만 manifest checksum을 반환합니다."""
    entries = tuple(artifacts)

    # 빈 목록과 중복 Artifact Key를 파일 읽기 전에 거부합니다.
    manifest_checksum = raw_manifest_checksum(metadata for _, metadata in entries)

    for file_path, metadata in entries:
        verify_raw_artifact(
            file_path=file_path,
            metadata=metadata,
        )

    return manifest_checksum


def _index_source_pages(
    result: SourceRunResult,
) -> dict[int, ProviderPage]:
    """수집 페이지를 검증하고 페이지 번호로 정리합니다."""
    pages_by_number: dict[int, ProviderPage] = {}

    for page in result.pages:
        if type(page.page_number) is not int or page.page_number < 1:
            raise ValueError("Source run has an invalid page number.")

        if page.page_number in pages_by_number:
            raise ValueError("Source run has a duplicate page number.")

        pages_by_number[page.page_number] = page

    return pages_by_number


def _index_raw_artifacts(
    artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
) -> dict[int, tuple[Path, RawArtifactMetadata]]:
    """원본 Artifact를 검증하고 페이지 번호로 정리합니다."""
    artifacts_by_page: dict[
        int,
        tuple[Path, RawArtifactMetadata],
    ] = {}

    for page_number, file_path, metadata in artifacts:
        if type(page_number) is not int or page_number < 1:
            raise ValueError("Raw artifact has an invalid page number.")

        if page_number in artifacts_by_page:
            raise ValueError("Raw artifact has a duplicate page binding.")

        artifacts_by_page[page_number] = (
            file_path,
            metadata,
        )

    return artifacts_by_page


def verify_source_run_artifacts(
    *,
    result: SourceRunResult,
    artifacts: Iterable[tuple[int, Path, RawArtifactMetadata]],
    decoder: ResponseDecoder,
    success_codes: tuple[str, ...],
    inspect_product_rejections: bool = False,
) -> VerifiedSourceRunArtifacts:
    """수집 페이지와 원본 Artifact가 정확히 대응하는지 검증합니다."""
    validator = _require_complete_product_pages if inspect_product_rejections else require_complete_source_run
    validator(result)

    pages_by_number = _index_source_pages(result)
    artifacts_by_page = _index_raw_artifacts(artifacts)

    if set(pages_by_number) != set(artifacts_by_page):
        raise ValueError("Source run pages do not match raw artifacts.")

    manifest_checksum = raw_manifest_checksum(metadata for _, metadata in artifacts_by_page.values())
    verified_records: list[Mapping[str, object]] = []

    for page_number in sorted(pages_by_number):
        page = pages_by_number[page_number]
        file_path, metadata = artifacts_by_page[page_number]

        if page.response_checksum != metadata.raw_checksum:
            raise ValueError("Source page checksum does not match raw artifact.")

        if page.content_type != metadata.content_type:
            raise ValueError("Source page content type does not match raw artifact.")

        content = read_verified_raw_artifact(
            file_path=file_path,
            metadata=metadata,
        )

        try:
            decoded = decoder(content, metadata.content_type)
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise ValueError("Raw artifact could not be decoded.") from None

        if decoded.body_code not in success_codes:
            raise ValueError("Raw artifact has a non-success body code.")

        if decoded.total_count != page.total_count:
            raise ValueError("Raw artifact total count does not match source page.")

        if decoded.page_number != page_number:
            raise ValueError("Raw artifact page number does not match source page.")

        serializer = _rejection_binding_bytes if inspect_product_rejections else canonical_json_bytes
        if serializer(list(decoded.records)) != serializer(list(page.records)):
            raise ValueError("Raw artifact records do not match source page.")

        verified_records.extend(decoded.records)

    return VerifiedSourceRunArtifacts(
        raw_manifest_checksum=manifest_checksum,
        records=tuple(verified_records),
    )


def _require_complete_product_pages(result: SourceRunResult) -> None:
    # Only PK eligibility is deferred to the two-pass classifier. Completeness is not relaxed.
    from ai_worker.tasks.rag.source_client.contracts import SourceRunStatus

    pages = result.pages
    count = result.record_count
    if (
        result.operation != PRODUCT_REJECT_IDENTITY
        or result.status is not SourceRunStatus.SUCCEEDED
        or result.failure is not None
        or not result.full_scan_completed
        or not pages
        or count <= 0
        or result.primary_key_validation is None
        or result.primary_key_validation.record_count != count
        or sorted(page.page_number for page in pages) != list(range(1, len(pages) + 1))
        or any(page.total_count != count for page in pages)
    ):
        raise ValueError("Product run is incomplete for rejection inspection.")


def _rejection_binding_bytes(value: object) -> bytes:
    # JSON type fidelity before identity validation: invalid ITEM_SEQ floats must reach
    # INVALID_ITEM_SEQ_TYPE rather than fail the checksum's narrower numeric contract.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
