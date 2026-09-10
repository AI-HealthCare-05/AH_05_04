from collections import Counter

import pytest

from ai_worker.tasks.rag.source_ingestion.checksums import product_canonical_checksum
from ai_worker.tasks.rag.source_ingestion.product_rejections import ProductIdentityError, classify_product_rejections


@pytest.mark.parametrize(
    "value,code",
    [
        (None, "ITEM_SEQ_REQUIRED"),
        ("", "ITEM_SEQ_REQUIRED"),
        (" \t", "ITEM_SEQ_REQUIRED"),
        (0, "INVALID_ITEM_SEQ_TYPE"),
        (True, "INVALID_ITEM_SEQ_TYPE"),
        ([], "INVALID_ITEM_SEQ_TYPE"),
        ({}, "INVALID_ITEM_SEQ_TYPE"),
        (1.0, "INVALID_ITEM_SEQ_TYPE"),
    ],
)
def test_classification_boundaries(value, code):
    (rejection,) = classify_product_rejections(((7, ({"ITEM_SEQ": value},)),))
    assert rejection.code == code
    assert rejection.parser_location == "page[7].record[0]"


def test_missing_key_and_all_duplicate_members_across_pages():
    pages = (
        (2, ({"ITEM_SEQ": "SYNTH_A"}, {}, {"ITEM_SEQ": "SYNTH_B"})),
        (1, ({"ITEM_SEQ": "SYNTH_A"}, {"ITEM_SEQ": "SYNTH_B"}, {"ITEM_SEQ": False})),
    )
    result = classify_product_rejections(pages)
    assert len(result) == 6
    assert Counter(r.code for r in result) == {
        "ITEM_SEQ_REQUIRED": 1,
        "INVALID_ITEM_SEQ_TYPE": 1,
        "DUPLICATE_ITEM_SEQ": 4,
    }
    assert [(r.page_number, r.record_index) for r in result] == [(1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)]
    reordered = tuple((n, tuple(reversed(rows))) for n, rows in reversed(pages))
    assert Counter(r.code for r in classify_product_rejections(reordered)) == Counter(r.code for r in result)
    assert "SYNTH_A" not in repr(result)


def test_original_strings_are_not_trimmed_or_coerced():
    records = ({"ITEM_SEQ": "01"}, {"ITEM_SEQ": "1"}, {"ITEM_SEQ": " 1"})
    assert classify_product_rejections(((1, records),)) == ()
    assert product_canonical_checksum(records) == product_canonical_checksum(reversed(records))


def test_checksum_uses_same_typed_rejection_not_exception_text():
    with pytest.raises(ProductIdentityError) as error:
        product_canonical_checksum(({}, {"ITEM_SEQ": "SYNTH"}, {"ITEM_SEQ": "SYNTH"}))
    assert len(error.value.rejections) == 3
    assert str(error.value) == "Product identity validation failed."


@pytest.mark.parametrize("pages", [((0, ()),), ((True, ()),), ((1, ()), (1, ()))])
def test_invalid_page_binding(pages):
    with pytest.raises(ValueError, match="binding"):
        classify_product_rejections(pages)
