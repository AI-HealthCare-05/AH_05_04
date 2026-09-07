"""Source 원본 및 제품 레코드의 checksum을 계산합니다."""

import hashlib
import unicodedata
from collections.abc import Iterable, Mapping

from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes


def raw_checksum(chunks: Iterable[bytes]) -> str:
    """원본 청크를 순서대로 연결한 바이트의 SHA-256을 반환합니다."""
    digest = hashlib.sha256()

    for chunk in chunks:
        digest.update(chunk)

    return digest.hexdigest()


def product_canonical_checksum(
    records: Iterable[Mapping[str, object]],
) -> str:
    """제품 레코드를 ITEM_SEQ로 정렬한 canonical JSON의 SHA-256입니다."""
    records_by_key: dict[str, dict[str, object]] = {}

    for record in records:
        item_seq = record.get("ITEM_SEQ")

        # 식별자를 숫자 등에서 문자열로 임의 변환하지 않습니다.
        if not isinstance(item_seq, str) or not item_seq.strip():
            raise ValueError("Product ITEM_SEQ must be a non-empty string.")

        # checksum용 JSON과 동일한 NFC 기준으로 정렬·중복 검사합니다.
        # 원본 ITEM_SEQ와 앞뒤 공백은 변경하지 않습니다.
        canonical_key = unicodedata.normalize("NFC", item_seq)

        if canonical_key in records_by_key:
            raise ValueError("Duplicate product ITEM_SEQ.")

        records_by_key[canonical_key] = dict(record)

    if not records_by_key:
        raise ValueError("Product records must not be empty.")

    ordered_records = [records_by_key[key] for key in sorted(records_by_key)]

    return hashlib.sha256(canonical_json_bytes(ordered_records)).hexdigest()


def raw_manifest_checksum(
    artifacts: Iterable[RawArtifactMetadata],
) -> str:
    """Artifact Key로 정렬한 원본 메타데이터 목록의 SHA-256입니다."""
    artifacts_by_key: dict[str, RawArtifactMetadata] = {}

    for artifact in artifacts:
        canonical_key = unicodedata.normalize("NFC", artifact.artifact_key)

        if canonical_key in artifacts_by_key:
            raise ValueError("Duplicate artifact key.")

        artifacts_by_key[canonical_key] = artifact

    if not artifacts_by_key:
        raise ValueError("Raw artifact manifest must not be empty.")

    manifest: list[list[object]] = []

    for key in sorted(artifacts_by_key):
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
