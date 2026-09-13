import hashlib
import unicodedata

import pytest

from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes


def test_sorts_object_keys_and_uses_compact_utf8_json() -> None:
    value = {"z": "합성", "a": {"y": 2, "x": 1}}

    assert canonical_json_bytes(value) == ('{"a":{"x":1,"y":2},"z":"합성"}'.encode())


def test_preserves_unicode_form_without_modifying_original() -> None:
    decomposed = unicodedata.normalize("NFD", "합성")
    original = {"name": decomposed}

    assert canonical_json_bytes(original) != canonical_json_bytes({"name": "합성"})
    assert original["name"] == decomposed


def test_preserves_whitespace_inside_string_values() -> None:
    assert canonical_json_bytes({"name": " 합성 "}) == ('{"name":" 합성 "}'.encode())
    assert canonical_json_bytes({"name": " 합성 "}) != (canonical_json_bytes({"name": "합성"}))


def test_distinguishes_numeric_strings_numbers_and_booleans() -> None:
    encoded = {canonical_json_bytes({"value": value}) for value in ("001", "1", 1, True)}

    assert len(encoded) == 4


def test_distinguishes_null_empty_string_and_missing_field() -> None:
    encoded = {
        canonical_json_bytes({"value": None}),
        canonical_json_bytes({"value": ""}),
        canonical_json_bytes({}),
    }

    assert len(encoded) == 3


def test_preserves_array_order() -> None:
    assert canonical_json_bytes({"values": [2, 1]}) == (b'{"values":[2,1]}')
    assert canonical_json_bytes({"values": [2, 1]}) != (canonical_json_bytes({"values": [1, 2]}))


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        0.5,
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_rejects_floating_point_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="Floating-point"):
        canonical_json_bytes({"nested": [value]})


def test_uses_evaluation_utf16_key_order() -> None:
    value = {
        "\ue000": 1,
        "\U00010000": 2,
    }

    assert canonical_json_bytes(value) == ('{"𐀀":2,"":1}'.encode())


def test_safe_integer_boundary_has_fixed_bytes_and_hash() -> None:
    value = {
        "minimum": -(2**53) + 1,
        "maximum": (2**53) - 1,
    }
    expected_bytes = b'{"maximum":9007199254740991,"minimum":-9007199254740991}'
    expected_sha256 = "fbfd7545b6e2e70fe97a1b1de6bb85a3cea527c9e49f787bb6392cd36964b62c"

    actual_bytes = canonical_json_bytes(value)

    assert actual_bytes == expected_bytes
    assert hashlib.sha256(actual_bytes).hexdigest() == expected_sha256


@pytest.mark.parametrize(
    "value",
    [
        -(2**53),
        2**53,
    ],
)
def test_rejects_integers_outside_safe_range(value: int) -> None:
    with pytest.raises(ValueError, match="safe range"):
        canonical_json_bytes({"value": value})


@pytest.mark.parametrize(
    "value",
    [
        {"value": "private\ud800value"},
        {"private\udfffkey": 1},
    ],
)
def test_rejects_lone_surrogates_without_exposing_value(
    value: object,
) -> None:
    with pytest.raises(ValueError) as captured:
        canonical_json_bytes(value)

    assert "surrogate" in str(captured.value)
    assert "private" not in str(captured.value)


@pytest.mark.parametrize(
    "value",
    [
        {1: "numeric-key"},
        {"value": b"bytes"},
        {"value": ("tuple",)},
    ],
)
def test_rejects_non_json_values(value: object) -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes(value)


def test_distinguishes_unicode_key_forms() -> None:
    composed = "합성"
    decomposed = unicodedata.normalize("NFD", composed)

    assert canonical_json_bytes({composed: 1}) != canonical_json_bytes({decomposed: 1})
