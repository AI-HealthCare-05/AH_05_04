import hashlib
import unicodedata

import pytest

from ai_worker.tasks.rag.source_ingestion.checksums import (
    product_canonical_checksum,
)


def test_hashes_records_in_item_seq_order() -> None:
    records = [
        {"ITEM_SEQ": "002"},
        {"ITEM_SEQ": "001"},
    ]
    expected_bytes = b'[{"ITEM_SEQ":"001"},{"ITEM_SEQ":"002"}]'

    assert product_canonical_checksum(records) == (hashlib.sha256(expected_bytes).hexdigest())


def test_record_order_does_not_change_checksum() -> None:
    records = [
        {"ITEM_SEQ": "002", "ITEM_NAME": "합성 제품 2"},
        {"ITEM_NAME": "합성 제품 1", "ITEM_SEQ": "001"},
    ]

    assert product_canonical_checksum(records) == (product_canonical_checksum(reversed(records)))
    assert records[0]["ITEM_SEQ"] == "002"


def test_distinguishes_unicode_value_forms() -> None:
    decomposed = unicodedata.normalize("NFD", "합성")
    original = [{"ITEM_SEQ": "001", "ITEM_NAME": decomposed}]
    composed = [{"ITEM_SEQ": "001", "ITEM_NAME": "합성"}]

    assert product_canonical_checksum(original) != (product_canonical_checksum(composed))
    assert original[0]["ITEM_NAME"] == decomposed


def test_preserves_field_content_in_checksum() -> None:
    variants: list[dict[str, object]] = [
        {"ITEM_SEQ": "001"},
        {"ITEM_SEQ": "001", "VALUE": None},
        {"ITEM_SEQ": "001", "VALUE": ""},
        {"ITEM_SEQ": "001", "VALUE": "1"},
        {"ITEM_SEQ": "001", "VALUE": 1},
        {"ITEM_SEQ": "001", "VALUE": " 1 "},
    ]

    checksums = {product_canonical_checksum([record]) for record in variants}

    assert len(checksums) == len(variants)


def test_rejects_empty_records() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        product_canonical_checksum([])


def test_rejects_missing_item_seq() -> None:
    with pytest.raises(ValueError, match="ITEM_SEQ"):
        product_canonical_checksum([{"ITEM_NAME": "합성 제품"}])


@pytest.mark.parametrize(
    "item_seq",
    [None, "", "   ", 1, True, 1.0],
)
def test_rejects_invalid_item_seq(item_seq: object) -> None:
    with pytest.raises(ValueError, match="ITEM_SEQ"):
        product_canonical_checksum([{"ITEM_SEQ": item_seq}])


def test_rejects_duplicate_identity_even_if_content_differs() -> None:
    records = [
        {"ITEM_SEQ": "001", "ITEM_NAME": "합성 제품 A"},
        {"ITEM_SEQ": "001", "ITEM_NAME": "합성 제품 B"},
    ]

    with pytest.raises(ValueError, match="Duplicate"):
        product_canonical_checksum(records)


def test_accepts_distinct_unicode_identity_forms() -> None:
    composed = "합성"
    decomposed = unicodedata.normalize("NFD", composed)
    records = [
        {"ITEM_SEQ": composed},
        {"ITEM_SEQ": decomposed},
    ]

    checksum = product_canonical_checksum(records)

    assert len(checksum) == 64
    assert checksum == product_canonical_checksum(reversed(records))
