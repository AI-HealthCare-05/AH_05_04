import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from ai_worker.tasks.rag.catalog import (
    CandidateCatalogSourceRef,
    CatalogBuildDecision,
    CatalogBuildRequest,
    build_catalog_candidate,
    build_catalog_members,
    create_catalog_export,
)
from ai_worker.tasks.rag.catalog.mfds_component import inspect_mfds_component_rows, map_mfds_component
from ai_worker.tasks.rag.catalog.validate import CatalogValidationFailureReason
from ai_worker.tests.rag.catalog.test_build import _ingredient_inputs, _product_inputs


def record():
    return dict(
        ITEM_SEQ="synthetic-product",
        TAMT_SEQ="01",
        MTRAL_SN="002",
        MTRAL_CODE="synthetic-material",
        QNT="010.00",
        INGD_UNIT_CD="mg",
        CPNT_CTNT_CONT="not-a-release-profile",
    )


def convert(row, **kwargs):
    return map_mfds_component(
        row,
        ingredient=_ingredient_inputs()[0],
        expected_material_code="synthetic-material",
        component_order=kwargs.get("order", 7),
    )


def test_mapping_preserves_original_serials_and_requires_independent_ingredient_identity():
    value = convert(record())
    assert json.loads(value.source_record_key)[1:] == ["synthetic-product", "01", "002"]
    assert value.ingredient_canonical_code == _ingredient_inputs()[0].canonical_code
    assert value.strength_value == "010.00"
    assert value.component_order == 7
    assert value.release_profile is None


@pytest.mark.parametrize("field", ["ITEM_SEQ", "TAMT_SEQ", "MTRAL_SN", "MTRAL_CODE", "QNT", "INGD_UNIT_CD"])
@pytest.mark.parametrize("missing", [None, "", " ", 1])
def test_incomplete_or_nonstring_source_values_are_not_filled(field, missing):
    with pytest.raises(ValueError):
        convert(dict(record(), **{field: missing}))


def test_mapping_mismatch_is_not_inferred_from_name_or_material_code():
    with pytest.raises(ValueError, match="mapping mismatch"):
        convert(dict(record(), MTRAL_CODE="different"))


@pytest.mark.parametrize("order", [True, 0, -1, "1"])
def test_order_is_not_inferred_from_provider_serial_or_array_position(order):
    with pytest.raises(ValueError, match="explicit positive order"):
        convert(record(), order=order)


def mapped_request(*, conflicting_key=False, separate_product_snapshot=False):
    """합성 입력의 매핑·순서는 명시적으로 제공한다. 실제 Loader가 아니다."""
    ingredient = _ingredient_inputs()[0]
    product = replace(_product_inputs()[0], canonical_code="synthetic-product")
    if separate_product_snapshot:
        product = replace(product, source_snapshot_id="synthetic-product-list-snapshot")
    second = dict(record(), MTRAL_SN="002" if conflicting_key else "003", QNT="020.00")
    return CatalogBuildRequest(
        catalog_version="synthetic-mfds-handoff",
        source_refs=tuple(
            CandidateCatalogSourceRef(snapshot, "synthetic-v1")
            for snapshot in sorted({product.source_snapshot_id, ingredient.source_snapshot_id})
        ),
        products=(product,),
        ingredients=(ingredient,),
        components=(convert(record(), order=1), convert(second, order=2)),
        aliases=(),
    )


def test_mapped_occurrences_reach_export_without_merging_same_material():
    request = mapped_request()

    def export(components):
        members = build_catalog_members(
            products=request.products, ingredients=request.ingredients, components=components, aliases=()
        )
        return create_catalog_export(
            catalog_version=request.catalog_version, source_refs=request.source_refs, members=members
        )

    original = export(request.components)
    replayed = export(tuple(reversed(request.components)) + (request.components[0],))
    assert original == replayed
    assert len({component.component_ref for component in original.catalog.components}) == 2
    assert {(c.component_order, c.strength_value) for c in original.catalog.components} == {
        (1, "010.00"),
        (2, "020.00"),
    }


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"conflicting_key": True}, CatalogValidationFailureReason.MEMBER_CONFLICT),
        ({"separate_product_snapshot": True}, CatalogValidationFailureReason.REFERENTIAL_INTEGRITY_INVALID),
    ],
)
async def test_mapped_invalid_references_stop_before_approval_and_save(options, reason):
    repository, verifier = AsyncMock(), AsyncMock()
    result = await build_catalog_candidate(
        request=mapped_request(**options), repository=repository, approval_verifier=verifier
    )
    assert result.decision is CatalogBuildDecision.REJECTED
    assert result.export is None
    assert any(failure.reason is reason for failure in result.validation.failures)
    verifier.verify.assert_not_awaited()
    repository.save_build.assert_not_awaited()


def test_inspection_preserves_total_groups_and_raw_fields_without_assigning_order():
    first = record()
    second = dict(first, TAMT_SEQ="02", MTRAL_SN="002", QNT="020.00")
    inspected = inspect_mfds_component_rows((first, second, dict(first)))
    assert inspected.eligible_for_mapping
    assert inspected.input_count == 3
    assert inspected.duplicate_count == 1
    assert len(inspected.observations) == 2
    assert {(row.tamt_seq, row.mtral_sn) for row in inspected.observations} == {("01", "002"), ("02", "002")}
    assert len({row.source_record_key for row in inspected.observations}) == 2
    assert json.loads(inspected.observations[0].record_json) == first
    first["TAMT_SEQ"] = "mutated-after-inspection"
    assert inspected.observations[0].tamt_seq == "01"
    assert json.loads(inspected.observations[0].record_json)["TAMT_SEQ"] == "01"


def test_blank_and_partial_rows_remain_visible_and_make_whole_input_ineligible():
    blank = {"ITEM_SEQ": "synthetic-empty", "MTRAL_CODE": None}
    partial = dict(record(), QNT=None)
    result = inspect_mfds_component_rows((record(), blank, partial))
    assert not result.eligible_for_mapping
    assert result.input_count == 3
    assert len(result.observations) == 1
    assert [row.reason for row in result.exclusions] == ["EMPTY_COMPONENT_FIELDS", "MISSING_COMPONENT_QUANTITY"]
    assert [json.loads(row.record_json) for row in result.exclusions] == [blank, partial]
    assert "synthetic-empty" not in repr(result.exclusions)


def test_blank_rows_alone_do_not_block_the_remaining_catalog_input():
    """#166 D-04 합의: 빈 주성분 행은 제외로만 남기고 정상 행의 Catalog 구성은 계속한다."""
    blank = {"ITEM_SEQ": "synthetic-empty", "MTRAL_CODE": None}
    result = inspect_mfds_component_rows((record(), blank))

    assert result.eligible_for_mapping
    assert result.has_excluded_empty_components
    assert result.input_count == 2
    assert [row.item_seq for row in result.observations] == ["synthetic-product"]
    assert [row.reason for row in result.empty_component_exclusions] == ["EMPTY_COMPONENT_FIELDS"]
    assert not result.blocking_exclusions
    assert json.loads(result.empty_component_exclusions[0].record_json) == blank


def test_blank_rows_never_become_observations_or_components():
    blank = {"ITEM_SEQ": "synthetic-empty", "MTRAL_CODE": None}
    result = inspect_mfds_component_rows((record(), blank))

    assert "synthetic-empty" not in {row.item_seq for row in result.observations}
    with pytest.raises(ValueError):
        convert(blank)


@pytest.mark.parametrize(
    ("extra_row", "expected_reason"),
    (
        (dict(record(), QNT=None), "MISSING_COMPONENT_QUANTITY"),
        (dict(record(), QNT="020.00"), "CONFLICTING_OBSERVATION"),
    ),
)
def test_integrity_violations_still_block_the_whole_input_next_to_blank_rows(extra_row, expected_reason):
    blank = {"ITEM_SEQ": "synthetic-empty", "MTRAL_CODE": None}
    result = inspect_mfds_component_rows((record(), blank, extra_row))

    assert not result.eligible_for_mapping
    assert [row.reason for row in result.blocking_exclusions] == [expected_reason]
    assert result.has_excluded_empty_components


def test_quantity_only_gap_is_separated_from_key_damage_but_still_blocks():
    """#166: 분량만 누락된 행과 identity 필드가 손상된 행을 구분하되 둘 다 차단한다."""
    quantity_gap = dict(record(), QNT=None)
    key_damage = dict(record(), MTRAL_SN=None)

    result = inspect_mfds_component_rows((record(), quantity_gap, key_damage))

    assert not result.eligible_for_mapping
    assert [row.reason for row in result.exclusions] == [
        "MISSING_COMPONENT_QUANTITY",
        "INVALID_COMPONENT_KEY_FIELDS",
    ]
    assert len(result.missing_quantity_exclusions) == 1
    assert json.loads(result.missing_quantity_exclusions[0].record_json) == quantity_gap
    # 분량 누락도 차단 사유로 남는다. 부분 Catalog는 별도 계약 없이 허용하지 않는다.
    assert len(result.blocking_exclusions) == 2


@pytest.mark.parametrize("damaged_field", ["TAMT_SEQ", "MTRAL_SN", "MTRAL_CODE", "INGD_UNIT_CD"])
def test_identity_field_damage_is_not_reported_as_a_quantity_gap(damaged_field):
    result = inspect_mfds_component_rows((dict(record(), **{damaged_field: None}),))

    assert result.exclusions[0].reason == "INVALID_COMPONENT_KEY_FIELDS"
    assert not result.missing_quantity_exclusions


def test_exclusion_counts_are_reported_by_reason():
    blank = {"ITEM_SEQ": "synthetic-empty", "MTRAL_CODE": None}
    result = inspect_mfds_component_rows((record(), blank, dict(record(), QNT=None)))

    assert result.exclusion_counts_by_reason == {
        "EMPTY_COMPONENT_FIELDS": 1,
        "MISSING_COMPONENT_QUANTITY": 1,
    }


def test_input_without_blank_rows_is_not_marked_partial():
    result = inspect_mfds_component_rows((record(),))

    assert result.eligible_for_mapping
    assert not result.has_excluded_empty_components
    assert not result.empty_component_exclusions


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_payload_is_not_last_wins_and_preserves_both_observations(reverse):
    rows = (record(), dict(record(), QNT="020.00"))
    result = inspect_mfds_component_rows(tuple(reversed(rows)) if reverse else rows)
    assert not result.eligible_for_mapping
    assert result.duplicate_count == 0
    assert result.exclusions[0].reason == "CONFLICTING_OBSERVATION"
    preserved = (*result.observations, *result.exclusions)
    assert {json.loads(row.record_json)["QNT"] for row in preserved} == {"010.00", "020.00"}


def test_empty_input_is_not_eligible_or_complete():
    result = inspect_mfds_component_rows(())
    assert result.input_count == 0
    assert not result.eligible_for_mapping


@pytest.mark.parametrize("item_seq", [None, "", " ", 123])
def test_missing_product_identity_is_not_classified_as_an_empty_component(item_seq):
    row = {"ITEM_SEQ": item_seq, "MTRAL_CODE": None}
    result = inspect_mfds_component_rows((row,))
    assert not result.eligible_for_mapping
    assert not result.observations
    assert result.exclusions[0].reason == "INVALID_COMPONENT_KEY_FIELDS"
    assert json.loads(result.exclusions[0].record_json) == row


@pytest.mark.parametrize("invalid", [float("nan"), object()])
def test_unrepresentable_record_fails_without_echoing_payload(invalid):
    with pytest.raises(ValueError, match="valid JSON object") as error:
        inspect_mfds_component_rows((dict(record(), sentinel="synthetic-secret-sentinel", invalid=invalid),))
    assert "synthetic-secret-sentinel" not in str(error.value)
