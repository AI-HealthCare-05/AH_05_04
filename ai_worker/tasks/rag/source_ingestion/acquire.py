"""수집한 원본 Artifact 목록의 무결성을 검증합니다."""

from collections.abc import Iterable
from pathlib import Path

from ai_worker.tasks.rag.source_client.contracts import (
    ProviderPage,
    SourceRunResult,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    verify_raw_artifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.validation import (
    require_complete_source_run,
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
) -> str:
    """수집 페이지와 원본 Artifact가 정확히 대응하는지 검증합니다."""
    require_complete_source_run(result)

    pages_by_number = _index_source_pages(result)
    artifacts_by_page = _index_raw_artifacts(artifacts)

    if set(pages_by_number) != set(artifacts_by_page):
        raise ValueError("Source run pages do not match raw artifacts.")

    for page_number, page in pages_by_number.items():
        _, metadata = artifacts_by_page[page_number]

        if page.response_checksum != metadata.raw_checksum:
            raise ValueError("Source page checksum does not match raw artifact.")

        if page.content_type != metadata.content_type:
            raise ValueError("Source page content type does not match raw artifact.")

    return verify_raw_artifact_manifest(artifacts_by_page.values())
