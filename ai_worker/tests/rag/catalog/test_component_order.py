"""D-04: v2 호환 순서 충돌 방어와 명시적 원본 키 반복 성분."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from ai_worker.tasks.rag.catalog import (
    CandidateCatalogSourceRef,
    CatalogBuildDecision,
    CatalogBuildRequest,
    CatalogExportError,
    build_catalog_candidate,
    build_catalog_members,
    create_catalog_export,
)
from ai_worker.tasks.rag.catalog.validate import CatalogValidationFailureReason, validate_catalog_members
from ai_worker.tests.rag.catalog.test_build import _component_inputs, _ingredient_inputs, _product_inputs


def members(components):
    return build_catalog_members(
        products=_product_inputs(), ingredients=_ingredient_inputs(), components=components, aliases=()
    )


def export(value):
    return create_catalog_export(
        catalog_version="synthetic-d04-review",
        members=value,
        source_refs=(CandidateCatalogSourceRef("synthetic-snapshot-001", "v1"),),
    )


def conflicting_inputs():
    first, second, third = _component_inputs()
    return first, second, replace(third, component_order=second.component_order)


def test_distinct_ingredients_cannot_share_one_product_order():
    forward = members(conflicting_inputs())
    backward = members(tuple(reversed(conflicting_inputs())))
    report = validate_catalog_members(forward)
    assert not report.is_valid
    assert report.conflict_count == 1
    assert report.failures[0].reason is CatalogValidationFailureReason.MEMBER_CONFLICT
    assert report == validate_catalog_members(backward)
    with pytest.raises(CatalogExportError):
        export(forward)


async def test_order_conflict_stops_before_approval_and_persistence():
    repository = AsyncMock()
    verifier = AsyncMock()
    verifier.verify.return_value = None
    result = await build_catalog_candidate(
        request=CatalogBuildRequest(
            catalog_version="synthetic-d04-review",
            source_refs=(CandidateCatalogSourceRef("synthetic-snapshot-001", "v1"),),
            products=_product_inputs(),
            ingredients=_ingredient_inputs(),
            components=conflicting_inputs(),
            aliases=(),
        ),
        repository=repository,
        approval_verifier=verifier,
    )
    assert result.decision is CatalogBuildDecision.REJECTED
    assert result.export is None
    repository.save_build.assert_not_awaited()
    verifier.verify.assert_not_awaited()


def test_explicit_orders_preserve_bytes_despite_input_permutation_and_exact_duplicates():
    inputs = _component_inputs()
    forward = export(members(inputs))
    backward = export(members(tuple(reversed(inputs)) + (inputs[0],)))
    assert forward.catalog_jsonl == backward.catalog_jsonl
    assert forward.export_checksum == backward.export_checksum
    assert forward.manifest_json == backward.manifest_json
    assert len(forward.catalog.components) == 3
    assert {c.strength_value for c in forward.catalog.components} == {"010.00", "5.0", "2.50"}


def test_legacy_repeated_ingredient_requires_explicit_source_keys():
    first = _component_inputs()[0]
    repeated = replace(first, component_order=2, strength_value="20", release_profile="SYNTHETIC_EXTENDED")
    value = members((first, repeated))
    assert len(value.components) == 2
    assert value.components[0].component_ref == value.components[1].component_ref
    assert not validate_catalog_members(value).is_valid
    with pytest.raises(CatalogExportError):
        export(value)


def test_storage_revalidates_component_order_even_with_old_validation_report():
    from ai_worker.tasks.rag.catalog.storage import CatalogStoragePreparationError, prepare_catalog_storage

    clean = members(_component_inputs())
    conflicting = members(conflicting_inputs())
    stale = create_catalog_export(
        catalog_version="synthetic-d04-review",
        members=conflicting,
        source_refs=(CandidateCatalogSourceRef("synthetic-snapshot-001", "v1"),),
        validation=validate_catalog_members(clean),
    )
    with pytest.raises(CatalogStoragePreparationError):
        prepare_catalog_storage(members=conflicting, artifacts=stale)


def test_explicit_source_keys_preserve_repeated_ingredient_occurrences():
    first = replace(_component_inputs()[0], source_record_key="synthetic:1:1")
    repeated = replace(
        first,
        source_record_key="synthetic:1:2",
        component_order=2,
        strength_value="020.00",
        release_profile="SYNTHETIC_EXTENDED",
    )
    value = members((first, repeated))
    assert validate_catalog_members(value).is_valid
    assert len({c.component_ref for c in value.components}) == 2
    assert export(value) == export(members((repeated, first, first)))
    assert {c.strength_value for c in value.components} == {"010.00", "020.00"}


def test_same_source_key_with_different_occurrence_content_is_rejected():
    first = replace(_component_inputs()[0], source_record_key="synthetic:1:1")
    repeated = replace(first, component_order=2, strength_value="020.00")
    with pytest.raises(CatalogExportError):
        export(members((first, repeated)))
