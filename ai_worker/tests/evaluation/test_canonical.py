from __future__ import annotations

from typing import Any

import pytest

from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
    normalize_resource_path,
    sha256_hex,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError


def test_canonical_json_uses_utf16_key_order_and_rejects_float() -> None:
    assert canonical_json_bytes({"\ue000": 1, "\U00010000": 2}) == '{"𐀀":2,"":1}'.encode()

    invalid_number: Any = 0.5
    with pytest.raises(EvaluationValidationError, match="EVAL_JSON_NUMBER_INVALID"):
        canonical_json_bytes({"ratio": invalid_number})


@pytest.mark.parametrize("value", [-(2**53), 2**53])
def test_canonical_json_rejects_integers_outside_ijson_safe_range(value: int) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        canonical_json_bytes({"value": value})

    assert caught.value.code is EvaluationErrorCode.JSON_NUMBER_INVALID


def test_canonical_json_rejects_lone_surrogates_without_echoing_them() -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        canonical_json_bytes({"text": "private\ud800value"})

    assert caught.value.code is EvaluationErrorCode.JSON_UNICODE_INVALID
    assert "private" not in str(caught.value)


def test_canonical_sha256_excludes_only_requested_top_level_keys() -> None:
    value: JsonValue = {"content": {"signature": "kept"}, "signature": "excluded"}

    assert canonical_sha256(value, excluded_top_level_keys=frozenset({"signature"})) == sha256_hex(
        b'{"content":{"signature":"kept"}}'
    )
    assert isinstance(value, dict)
    assert value["signature"] == "excluded"


def test_sha256_hex_returns_raw_lowercase_digest() -> None:
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_normalize_resource_path_uses_nfc() -> None:
    assert normalize_resource_path("fixtures/cafe\u0301.json") == "fixtures/café.json"


@pytest.mark.parametrize(
    "value",
    [
        "/absolute.json",
        "C:/absolute.json",
        "fixtures\\case.json",
        "fixtures/../case.json",
        "fixtures/./case.json",
        "fixtures//case.json",
        "fixtures/case\x00.json",
    ],
)
def test_normalize_resource_path_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises(EvaluationValidationError, match="EVAL_RESOURCE_PATH_INVALID"):
        normalize_resource_path(value)


def test_validation_error_formats_only_code_and_safe_path() -> None:
    error = EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID, safe_path="/resources/0")

    assert str(error) == "EVAL_RESOURCE_PATH_INVALID at /resources/0"


def test_canonical_json_explicit_null_differs_from_missing_key() -> None:
    with_null: JsonValue = {"optional_field": None}
    without_field: JsonValue = {}

    bytes_with_null = canonical_json_bytes(with_null)
    bytes_without_field = canonical_json_bytes(without_field)

    assert bytes_with_null == b'{"optional_field":null}'
    assert bytes_without_field == b"{}"
    assert canonical_sha256(with_null) != canonical_sha256(without_field)


def test_canonical_json_preserves_nfc_and_nfd_without_auto_normalization() -> None:
    # RFC 8785 Section 3.2.2: JCS does NOT normalize Unicode strings (e.g., NFD -> NFC).
    # Normalization must be performed at application input boundaries.
    nfc_text = "caf\u00e9"  # 'café' in NFC
    nfd_text = "cafe\u0301"  # 'café' in NFD (e + combining acute accent)

    assert nfc_text != nfd_text
    nfc_bytes = canonical_json_bytes({"name": nfc_text})
    nfd_bytes = canonical_json_bytes({"name": nfd_text})

    assert nfc_bytes == b'{"name":"caf\xc3\xa9"}'
    assert nfd_bytes == b'{"name":"cafe\xcc\x81"}'
    assert nfc_bytes != nfd_bytes
    assert canonical_sha256({"name": nfc_text}) != canonical_sha256({"name": nfd_text})
