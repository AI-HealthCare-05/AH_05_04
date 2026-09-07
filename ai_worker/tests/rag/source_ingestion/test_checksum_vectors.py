"""직렬화 바이트와 SHA-256 기대값을 고정한 합성 테스트 벡터입니다."""

import hashlib

from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.checksums import (
    product_canonical_checksum,
    raw_manifest_checksum,
)
from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes


def test_json_serialization_vector() -> None:
    value = {
        "null": None,
        "empty": "",
        "b": "001",
        "a": 1,
    }
    expected_bytes = b'{"a":1,"b":"001","empty":"","null":null}'
    expected_sha256 = "9145fd8cc6da2da82fbc7567fa40ee168747951980a8659e75bb449ea7347e64"

    actual_bytes = canonical_json_bytes(value)

    assert actual_bytes == expected_bytes
    assert hashlib.sha256(actual_bytes).hexdigest() == expected_sha256


def test_product_checksum_vector() -> None:
    # 고정한 바이트열과 제품 checksum 함수를 같은 기대값으로 확인합니다.
    records = [
        {"ITEM_SEQ": "002"},
        {"ITEM_SEQ": "001"},
    ]
    expected_bytes = b'[{"ITEM_SEQ":"001"},{"ITEM_SEQ":"002"}]'
    expected_sha256 = "69a9513d80af6159c80b5d11bece974bf1dbe02f5d08a76520819c3ac36a5524"

    # 固定したバイト列と、製品 checksum 関数の両方を同じ期待値で確認します。
    assert hashlib.sha256(expected_bytes).hexdigest() == expected_sha256
    assert product_canonical_checksum(records) == expected_sha256


def test_raw_manifest_checksum_vector() -> None:
    artifact = RawArtifactMetadata(
        artifact_key="synthetic/a.json",
        raw_checksum="a" * 64,
        byte_size=10,
        content_type="application/json",
    )
    expected_bytes = (
        b'[["synthetic/a.json",'
        b'"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        b'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'10,"application/json"]]'
    )
    expected_sha256 = "d3e5493325749ed11a84c5269b479d5a12c70d38638bd28d1ec876f7e0971bc7"

    assert hashlib.sha256(expected_bytes).hexdigest() == expected_sha256
    assert raw_manifest_checksum([artifact]) == expected_sha256
