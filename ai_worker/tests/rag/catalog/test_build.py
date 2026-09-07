import json
from pathlib import Path
from typing import cast

import pytest

from ai_worker.tasks.rag.catalog import (
    CandidateAliasReviewStatus,
    CandidateEntityType,
    CandidateEntryType,
    CandidateRecordStatus,
    CatalogAliasInput,
    CatalogComponentInput,
    CatalogComponentRole,
    CatalogMappingError,
    CatalogProductInput,
    build_catalog_members,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "rag" / "catalog"


def _records(name: str) -> list[dict[str, object]]:
    document = json.loads((_FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    records = document["records"]
    assert isinstance(records, list)
    assert all(isinstance(record, dict) for record in records)
    return cast(list[dict[str, object]], records)


def _text(record: dict[str, object], key: str) -> str:
    value = record[key]
    assert isinstance(value, str)
    return value


def _optional_text(record: dict[str, object], key: str) -> str | None:
    value = record[key]
    assert value is None or isinstance(value, str)
    return value


def _product_inputs() -> tuple[CatalogProductInput, ...]:
    return tuple(
        CatalogProductInput(
            source_snapshot_id=_text(record, "source_snapshot_id"),
            source_record_key=_text(record, "source_record_key"),
            code_system=_text(record, "code_system"),
            canonical_code=_text(record, "canonical_code"),
            product_name=_text(record, "product_name"),
            strength_text=_optional_text(record, "strength_text"),
            dosage_form=_optional_text(record, "dosage_form"),
            manufacturer_name=_optional_text(record, "manufacturer_name"),
            product_status=CandidateRecordStatus(_text(record, "product_status")),
        )
        for record in _records("synthetic_products.json")
    )


def _component_inputs() -> tuple[CatalogComponentInput, ...]:
    inputs: list[CatalogComponentInput] = []
    for record in _records("synthetic_components.json"):
        component_order = record["component_order"]
        assert type(component_order) is int
        inputs.append(
            CatalogComponentInput(
                source_snapshot_id=_text(record, "source_snapshot_id"),
                product_code_system=_text(record, "product_code_system"),
                product_canonical_code=_text(record, "product_code"),
                ingredient_code_system=_text(record, "ingredient_code_system"),
                ingredient_canonical_code=_text(record, "ingredient_code"),
                ingredient_name=_text(record, "ingredient_name"),
                component_role=CatalogComponentRole(_text(record, "component_role")),
                component_order=component_order,
                strength_value=_text(record, "strength_value"),
                strength_unit=_text(record, "strength_unit"),
            )
        )
    return tuple(inputs)


def _alias_inputs() -> tuple[CatalogAliasInput, ...]:
    inputs: list[CatalogAliasInput] = []
    for record in _records("synthetic_aliases.json"):
        is_effective = record["is_effective"]
        assert type(is_effective) is bool
        inputs.append(
            CatalogAliasInput(
                source_snapshot_id=_text(record, "source_snapshot_id"),
                target_source_snapshot_id=_text(record, "target_source_snapshot_id"),
                source_alias_ref=_text(record, "alias_ref"),
                target_type=CandidateEntityType(_text(record, "target_type")),
                target_code_system=_text(record, "target_code_system"),
                target_canonical_code=_text(record, "target_code"),
                alias_source=_text(record, "alias_source"),
                alias_text=_text(record, "alias_text"),
                review_status=CandidateAliasReviewStatus(_text(record, "review_status")),
                status=CandidateRecordStatus(_text(record, "status")),
                is_effective=is_effective,
            )
        )
    return tuple(inputs)


def _build_fixture():
    return build_catalog_members(
        products=_product_inputs(),
        components=_component_inputs(),
        aliases=_alias_inputs(),
    )


def test_builds_same_name_products_as_distinct_official_identities() -> None:
    catalog = _build_fixture()

    same_name_products = [product for product in catalog.products if product.product_name == "합성 제품 A정"]

    assert len(same_name_products) == 2
    assert {product.identity.canonical_code for product in same_name_products} == {"000000001", "000000002"}
    assert len({product.product_ref for product in same_name_products}) == 2


def test_builds_single_and_multi_ingredient_components_in_source_order() -> None:
    catalog = _build_fixture()
    product_refs = {product.identity.canonical_code: product.product_ref for product in catalog.products}

    first = [component for component in catalog.components if component.product_ref == product_refs["000000001"]]
    second = [component for component in catalog.components if component.product_ref == product_refs["000000002"]]

    assert [(component.component_order, component.strength_value) for component in first] == [(1, "010.00")]
    assert [(component.component_order, component.strength_value) for component in second] == [(1, "5.0"), (2, "2.50")]


def test_exports_only_approved_effective_product_aliases_as_alias_entries() -> None:
    catalog = _build_fixture()
    alias_entries = [entry for entry in catalog.search_entries if entry.entry_type is CandidateEntryType.APPROVED_ALIAS]

    assert {entry.display_text for entry in alias_entries} == {"합성 제품 별칭", "합성 충돌 별칭"}
    assert all(entry.identity.entity_type is CandidateEntityType.PRODUCT for entry in alias_entries)
    assert "합성 성분 별칭" not in {entry.display_text for entry in alias_entries}
    assert "합성 만료 별칭" not in {entry.display_text for entry in alias_entries}


def test_exact_duplicate_rows_are_deduplicated_and_input_order_does_not_change_refs() -> None:
    products = _product_inputs()
    components = _component_inputs()
    aliases = _alias_inputs()

    original = build_catalog_members(products=products, components=components, aliases=aliases)
    reordered = build_catalog_members(
        products=tuple(reversed(products)) + (products[0],),
        components=tuple(reversed(components)) + (components[0],),
        aliases=tuple(reversed(aliases)) + (aliases[0],),
    )

    assert {product.product_ref for product in original.products} == {
        product.product_ref for product in reordered.products
    }
    assert {component.component_ref for component in original.components} == {
        component.component_ref for component in reordered.components
    }
    assert {alias.alias_ref for alias in original.aliases} == {alias.alias_ref for alias in reordered.aliases}
    assert len(original.products) == len(reordered.products)
    assert len(original.components) == len(reordered.components)
    assert len(original.aliases) == len(reordered.aliases)


def test_component_natural_key_keeps_role_distinct() -> None:
    base = _component_inputs()[0]
    excipient = CatalogComponentInput(
        source_snapshot_id=base.source_snapshot_id,
        product_code_system=base.product_code_system,
        product_canonical_code=base.product_canonical_code,
        ingredient_code_system=base.ingredient_code_system,
        ingredient_canonical_code=base.ingredient_canonical_code,
        ingredient_name=base.ingredient_name,
        component_role=CatalogComponentRole.EXCIPIENT,
        component_order=2,
        strength_value=base.strength_value,
        strength_unit=base.strength_unit,
    )

    catalog = build_catalog_members(products=_product_inputs(), components=(base, excipient), aliases=())

    assert len(catalog.components) == 2
    assert len({component.component_ref for component in catalog.components}) == 2


def test_alias_source_snapshot_can_differ_from_target_product_snapshot() -> None:
    alias = _alias_inputs()[0]
    cross_snapshot_alias = CatalogAliasInput(
        source_snapshot_id="synthetic-alias-snapshot-002",
        target_source_snapshot_id=alias.target_source_snapshot_id,
        source_alias_ref=alias.source_alias_ref,
        target_type=alias.target_type,
        target_code_system=alias.target_code_system,
        target_canonical_code=alias.target_canonical_code,
        alias_source=alias.alias_source,
        alias_text=alias.alias_text,
        review_status=alias.review_status,
        status=alias.status,
        is_effective=alias.is_effective,
    )

    catalog = build_catalog_members(products=_product_inputs(), components=(), aliases=(cross_snapshot_alias,))

    assert catalog.aliases[0].source_snapshot_id == "synthetic-alias-snapshot-002"
    alias_entry = next(
        entry for entry in catalog.search_entries if entry.entry_type is CandidateEntryType.APPROVED_ALIAS
    )
    assert alias_entry.source_snapshot_id == "synthetic-alias-snapshot-002"


def test_missing_component_product_fails_without_exposing_source_value() -> None:
    component = _component_inputs()[0]
    missing = CatalogComponentInput(
        source_snapshot_id=component.source_snapshot_id,
        product_code_system=component.product_code_system,
        product_canonical_code="SENSITIVE-MISSING-PRODUCT",
        ingredient_code_system=component.ingredient_code_system,
        ingredient_canonical_code=component.ingredient_canonical_code,
        ingredient_name=component.ingredient_name,
        component_role=component.component_role,
        component_order=component.component_order,
        strength_value=component.strength_value,
        strength_unit=component.strength_unit,
    )

    with pytest.raises(CatalogMappingError) as captured:
        build_catalog_members(products=_product_inputs(), components=(missing,), aliases=())

    assert captured.value.code == "COMPONENT_PRODUCT_NOT_FOUND"
    assert captured.value.paths == ("components.product_identity",)
    assert "SENSITIVE" not in str(captured.value)


def test_missing_alias_target_fails_closed() -> None:
    alias = _alias_inputs()[0]
    missing = CatalogAliasInput(
        source_snapshot_id=alias.source_snapshot_id,
        target_source_snapshot_id=alias.target_source_snapshot_id,
        source_alias_ref=alias.source_alias_ref,
        target_type=alias.target_type,
        target_code_system=alias.target_code_system,
        target_canonical_code="MISSING",
        alias_source=alias.alias_source,
        alias_text=alias.alias_text,
        review_status=alias.review_status,
        status=alias.status,
        is_effective=alias.is_effective,
    )

    with pytest.raises(CatalogMappingError, match="ALIAS_TARGET_NOT_FOUND"):
        build_catalog_members(products=_product_inputs(), components=(), aliases=(missing,))
