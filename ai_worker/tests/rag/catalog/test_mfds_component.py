import json

import pytest

from ai_worker.tasks.rag.catalog.mfds_component import map_mfds_component
from ai_worker.tests.rag.catalog.test_build import _ingredient_inputs


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
