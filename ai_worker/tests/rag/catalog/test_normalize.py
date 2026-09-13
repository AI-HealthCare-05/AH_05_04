import json
import unicodedata
from pathlib import Path

import pytest

from ai_worker.tasks.rag.catalog.normalize import (
    CATALOG_NORMALIZATION_VERSION,
    normalize_catalog_text,
    normalize_optional_catalog_text,
    require_official_identity_text,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "rag" / "catalog"


def _load_fixture(name: str) -> dict[str, object]:
    value = json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _records(name: str) -> list[dict[str, object]]:
    value = _load_fixture(name)["records"]
    assert isinstance(value, list)
    assert all(isinstance(record, dict) for record in value)
    return value


def test_normalizes_nfc_and_whitespace_without_changing_raw_value() -> None:
    raw_value = f"  {unicodedata.normalize('NFD', '합성')}\t  제품\nA정  "

    result = normalize_catalog_text(raw_value, field_name="product_name")

    assert result.raw_value == raw_value
    assert result.normalized_value == "합성 제품 A정"
    assert unicodedata.is_normalized("NFC", result.normalized_value)
    assert result.normalization_version == CATALOG_NORMALIZATION_VERSION


def test_numeric_text_remains_a_string_and_keeps_its_representation() -> None:
    result = normalize_catalog_text("  001.00  ", field_name="strength_text")

    assert result.normalized_value == "001.00"
    assert isinstance(result.normalized_value, str)


def test_optional_text_distinguishes_null_from_blank() -> None:
    assert normalize_optional_catalog_text(None, field_name="dosage_form") is None

    with pytest.raises(ValueError, match="must not be blank"):
        normalize_optional_catalog_text(" \t\n ", field_name="dosage_form")


@pytest.mark.parametrize("value", [None, 1, 1.0, True, [], {}])
def test_rejects_non_string_catalog_text(value: object) -> None:
    with pytest.raises(ValueError, match="must be a string"):
        normalize_catalog_text(value, field_name="product_name")


def test_rejects_invalid_unicode_without_exposing_value() -> None:
    with pytest.raises(ValueError, match="product_name must be valid Unicode text") as captured:
        normalize_catalog_text("synthetic\ud800value", field_name="product_name")

    assert "synthetic" not in str(captured.value)


def test_official_identity_is_validated_without_transformation() -> None:
    value = "000000001"

    assert require_official_identity_text(value, field_name="canonical_code") is value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (" 000000001", "already trimmed"),
        ("000000001 ", "already trimmed"),
        ("", "nonblank"),
        (unicodedata.normalize("NFD", "합성코드"), "NFC"),
    ],
)
def test_official_identity_rejects_values_that_need_transformation(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        require_official_identity_text(value, field_name="canonical_code")


def test_product_fixture_keeps_same_name_products_with_distinct_official_codes() -> None:
    records = _records("synthetic_products.json")
    same_name_records = records[:2]

    names = {
        normalize_catalog_text(record["product_name"], field_name="product_name").normalized_value
        for record in same_name_records
    }
    codes = {record["canonical_code"] for record in same_name_records}

    assert names == {"합성 제품 A정"}
    assert codes == {"000000001", "000000002"}


def test_component_fixture_contains_single_and_multi_ingredient_products() -> None:
    records = _records("synthetic_components.json")
    component_counts: dict[object, int] = {}
    for record in records:
        product_code = record["product_code"]
        component_counts[product_code] = component_counts.get(product_code, 0) + 1

    assert component_counts == {"000000001": 1, "000000002": 2}
    assert [record["component_order"] for record in records if record["product_code"] == "000000002"] == [1, 2]
    assert records[0]["strength_value"] == "010.00"


def test_alias_fixture_covers_product_ingredient_conflict_and_expiry() -> None:
    records = _records("synthetic_aliases.json")

    assert {record["target_type"] for record in records} == {"PRODUCT", "INGREDIENT"}
    conflict_targets = {record["target_code"] for record in records if record["alias_text"] == "합성 충돌 별칭"}
    assert conflict_targets == {"000000001", "000000002"}
    assert any(record["is_effective"] is False for record in records)
