"""직렬화 바이트와 SHA-256 기대값을 고정한 합성 테스트 벡터입니다."""

import hashlib

from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.checksums import (
    product_canonical_checksum,
    raw_manifest_checksum,
)
from ai_worker.tasks.rag.source_ingestion.normalize import (
    canonical_json_bytes,
    utf16_sort_key,
)


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

    # 고정한 바이트열과 제품 checksum 함수를 같은 기대값으로 확인합니다.
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


# non-BMP 경계 벡터: code point 순서와 UTF-16 code unit 순서가 어긋나는 최소 쌍입니다.
# U+1F600은 UTF-16에서 surrogate pair 0xD83D 0xDE00이고 U+FF21은 단일 code unit 0xFF21입니다.
# 따라서 code point 순서는 U+FF21이 앞이지만 canonical comparator(UTF-16)는 U+1F600이 앞입니다.
_NON_BMP_ITEM_SEQ = "\U0001f600"
_BMP_ITEM_SEQ = "Ａ"


def test_canonical_comparator_differs_from_code_point_order() -> None:
    # 아래 벡터가 comparator를 실제로 고정하는지(무의미해지지 않았는지) 먼저 확인합니다.
    values = [_BMP_ITEM_SEQ, _NON_BMP_ITEM_SEQ]

    assert sorted(values) == [_BMP_ITEM_SEQ, _NON_BMP_ITEM_SEQ]
    assert sorted(values, key=utf16_sort_key) == [_NON_BMP_ITEM_SEQ, _BMP_ITEM_SEQ]


def test_product_checksum_non_bmp_boundary_vector() -> None:
    records = [
        {"ITEM_SEQ": _BMP_ITEM_SEQ},
        {"ITEM_SEQ": _NON_BMP_ITEM_SEQ},
    ]
    # UTF-16 code unit 순서이므로 non-BMP ITEM_SEQ가 앞에 옵니다.
    expected_bytes = b'[{"ITEM_SEQ":"\xf0\x9f\x98\x80"},{"ITEM_SEQ":"\xef\xbc\xa1"}]'
    expected_sha256 = "98fe84759c8bc2a9121aaf76a9dd24d30adec948afb5debf32033b6e2eaaa5a7"

    assert hashlib.sha256(expected_bytes).hexdigest() == expected_sha256
    assert product_canonical_checksum(records) == expected_sha256

    # code point 순서로 정렬한 직렬화와 실제로 다른 값이어야 합니다.
    code_point_ordered = canonical_json_bytes([{"ITEM_SEQ": _BMP_ITEM_SEQ}, {"ITEM_SEQ": _NON_BMP_ITEM_SEQ}])
    assert hashlib.sha256(code_point_ordered).hexdigest() != expected_sha256


def test_json_object_key_non_bmp_boundary_vector() -> None:
    value = {_BMP_ITEM_SEQ: 1, _NON_BMP_ITEM_SEQ: 2}
    expected_bytes = b'{"\xf0\x9f\x98\x80":2,"\xef\xbc\xa1":1}'
    expected_sha256 = "983ec72f503aa05fb485ea06724aef2c8cd88a98b801dbc3518ca3cb6fa34bbd"

    actual_bytes = canonical_json_bytes(value)

    assert actual_bytes == expected_bytes
    assert hashlib.sha256(actual_bytes).hexdigest() == expected_sha256


def test_raw_manifest_checksum_non_bmp_boundary_vector() -> None:
    artifacts = [
        RawArtifactMetadata(
            artifact_key=f"synthetic/{_BMP_ITEM_SEQ}.json",
            raw_checksum="b" * 64,
            byte_size=11,
            content_type="application/json",
        ),
        RawArtifactMetadata(
            artifact_key=f"synthetic/{_NON_BMP_ITEM_SEQ}.json",
            raw_checksum="c" * 64,
            byte_size=12,
            content_type="application/json",
        ),
    ]

    # Artifact Key도 같은 comparator를 쓰므로 non-BMP Key가 앞에 옵니다.
    ordered = raw_manifest_checksum(artifacts)
    reversed_order = raw_manifest_checksum(list(reversed(artifacts)))

    assert ordered == reversed_order

    expected_bytes = canonical_json_bytes(
        [
            [f"synthetic/{_NON_BMP_ITEM_SEQ}.json", "c" * 64, 12, "application/json"],
            [f"synthetic/{_BMP_ITEM_SEQ}.json", "b" * 64, 11, "application/json"],
        ]
    )
    assert ordered == hashlib.sha256(expected_bytes).hexdigest()
