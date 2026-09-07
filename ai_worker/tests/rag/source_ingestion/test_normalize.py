import unicodedata

import pytest

from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes


def test_sorts_object_keys_and_uses_compact_utf8_json() -> None:
    value = {"z": "합성", "a": {"y": 2, "x": 1}}

    assert canonical_json_bytes(value) == ('{"a":{"x":1,"y":2},"z":"합성"}'.encode())


def test_normalizes_unicode_without_modifying_original() -> None:
    decomposed = unicodedata.normalize("NFD", "합성")
    original = {"name": decomposed}

    assert canonical_json_bytes(original) == canonical_json_bytes({"name": "합성"})
    assert original["name"] == decomposed
    assert original["name"] != "합성"


def test_preserves_whitespace_inside_string_values() -> None:
    assert canonical_json_bytes({"name": " 합성 "}) == ('{"name":" 합성 "}'.encode())
    assert canonical_json_bytes({"name": " 합성 "}) != (canonical_json_bytes({"name": "합성"}))


def test_distinguishes_numeric_strings_numbers_and_booleans() -> None:
    encoded = {canonical_json_bytes({"value": value}) for value in ("001", "1", 1, 1.0, True)}

    assert len(encoded) == 5


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
    [float("nan"), float("inf"), float("-inf")],
)
def test_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"nested": [value]})


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


def test_rejects_keys_that_collide_after_nfc() -> None:
    composed = "합성"
    decomposed = unicodedata.normalize("NFD", composed)

    with pytest.raises(ValueError, match="collide"):
        canonical_json_bytes({composed: 1, decomposed: 2})
