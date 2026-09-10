"""Source 원본 및 제품 레코드의 checksum을 계산합니다."""

import hashlib
from collections.abc import Iterable, Mapping
from typing import cast

from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.normalize import (
    canonical_json_bytes,
    utf16_sort_key,
)
from ai_worker.tasks.rag.source_ingestion.product_rejections import ProductIdentityError, classify_product_rejections


def raw_checksum(chunks: Iterable[bytes]) -> str:
    """원본 청크를 순서대로 연결한 바이트의 SHA-256을 반환합니다."""
    digest = hashlib.sha256()

    for chunk in chunks:
        digest.update(chunk)

    return digest.hexdigest()


def product_canonical_checksum(
    records: Iterable[Mapping[str, object]],
) -> str:
    """제품 레코드를 ITEM_SEQ로 정렬한 canonical JSON의 SHA-256입니다.

    정렬 기준은 객체 key와 같은 UTF-16 code unit 순서(`utf16_sort_key`)입니다.
    Python 기본 문자열 비교(code point 순서)를 쓰면 non-BMP ITEM_SEQ에서 다른
    언어 구현과 순서가 갈려 같은 입력이 다른 checksum을 냅니다.
    """
    entries = tuple(records)
    rejections = classify_product_rejections(((1, entries),))
    if rejections:
        raise ProductIdentityError(rejections)
    records_by_key = {cast(str, record["ITEM_SEQ"]): dict(record) for record in entries}

    if not records_by_key:
        raise ValueError("Product records must not be empty.")

    ordered_records = [records_by_key[key] for key in sorted(records_by_key, key=utf16_sort_key)]

    return hashlib.sha256(canonical_json_bytes(ordered_records)).hexdigest()


def raw_manifest_checksum(
    artifacts: Iterable[RawArtifactMetadata],
) -> str:
    """Artifact Key로 정렬한 원본 메타데이터 목록의 SHA-256입니다.

    Artifact Key도 제품 레코드와 같은 UTF-16 code unit 순서로 정렬합니다.
    """
    artifacts_by_key: dict[str, RawArtifactMetadata] = {}

    for artifact in artifacts:
        if artifact.artifact_key in artifacts_by_key:
            raise ValueError("Duplicate artifact key.")

        artifacts_by_key[artifact.artifact_key] = artifact

    if not artifacts_by_key:
        raise ValueError("Raw artifact manifest must not be empty.")

    manifest: list[list[object]] = []

    for key in sorted(artifacts_by_key, key=utf16_sort_key):
        artifact = artifacts_by_key[key]
        manifest.append(
            [
                artifact.artifact_key,
                artifact.raw_checksum,
                artifact.byte_size,
                artifact.content_type,
            ]
        )

    return hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
