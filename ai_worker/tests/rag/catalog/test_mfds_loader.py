import hashlib
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ai_worker.tasks.rag.catalog.export import _canonical_json_bytes
from ai_worker.tasks.rag.catalog.mfds_component import inspect_mfds_component_rows
from ai_worker.tasks.rag.catalog.mfds_loader import DETAIL_CANONICALIZATION_SPEC, load_mfds_catalog
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
)
from ai_worker.tests.rag.catalog.test_build import _ingredient_inputs, _product_inputs
from ai_worker.tests.rag.catalog.test_mfds_component import record


def receipt(snapshot_id, checksum):
    return SnapshotProvenanceReceipt(
        source_id=uuid4(),
        source_code="SYNTHETIC_MFDS",
        endpoint_id=uuid4(),
        operation_id=uuid4(),
        source_snapshot_id=snapshot_id,
        source_version="external:synthetic-v1",
        external_version="synthetic-v1",
        canonical_checksum=checksum,
        canonicalization_spec_version=DETAIL_CANONICALIZATION_SPEC,
        endpoint_receipt_hash="a" * 64,
        verification_seal_id=uuid4(),
        verification_status=SnapshotVerificationStatus.PENDING,
        rejected_record_count=0,
        publication_verification_id=None,
    )


def inputs():
    product_id, detail_id = uuid4(), uuid4()
    raw = _canonical_json_bytes([record()])
    observation = inspect_mfds_component_rows((record(),)).observations[0]
    return dict(
        catalog_version="synthetic-loader-test",
        detail_receipt=receipt(detail_id, hashlib.sha256(raw).hexdigest()),
        detail_json=raw,
        product_receipt=receipt(product_id, "b" * 64),
        products=(
            replace(_product_inputs()[0], source_snapshot_id=str(product_id), canonical_code="synthetic-product"),
        ),
        ingredients_by_material={
            "synthetic-material": replace(
                _ingredient_inputs()[0], source_snapshot_id=str(detail_id), canonical_code="synthetic-material"
            )
        },
        orders_by_observation={observation.source_record_key: 1},
        order_spec_version="synthetic-explicit-v1",
        repository=AsyncMock(),
        approval_verifier=AsyncMock(),
    )


@pytest.mark.parametrize(
    "damage",
    [
        "product-source",
        "product-scope",
        "ingredient-source",
        "ingredient-code",
        "mapping-scope",
        "order-scope",
        "order-value",
        "order-version",
        "receipt",
        "checksum",
        "canonical-spec",
        "canonical-bytes",
        "duplicate-json-key",
    ],
)
async def test_invalid_loader_input_never_requests_approval_or_writes(damage):
    kwargs = inputs()
    if damage in ("product-source", "product-scope"):
        fields = (
            {"source_snapshot_id": str(uuid4())}
            if damage == "product-source"
            else {"canonical_code": "another-product"}
        )
        kwargs["products"] = (replace(kwargs["products"][0], **fields),)
    elif damage in ("ingredient-source", "ingredient-code"):
        ingredient = kwargs["ingredients_by_material"]["synthetic-material"]
        fields = (
            {"source_snapshot_id": str(uuid4())}
            if damage == "ingredient-source"
            else {"canonical_code": "another-code"}
        )
        kwargs["ingredients_by_material"] = {"synthetic-material": replace(ingredient, **fields)}
    elif damage == "mapping-scope":
        kwargs["ingredients_by_material"] = {}
    elif damage == "order-scope":
        kwargs["orders_by_observation"] = {}
    elif damage == "order-value":
        kwargs["orders_by_observation"] = {key: True for key in kwargs["orders_by_observation"]}
    elif damage == "order-version":
        kwargs["order_spec_version"] = ""
    elif damage == "receipt":
        kwargs["detail_receipt"] = replace(kwargs["detail_receipt"], endpoint_receipt_hash=None)
    elif damage == "canonical-spec":
        kwargs["detail_receipt"] = replace(kwargs["detail_receipt"], canonicalization_spec_version="unrelated-v1")
    else:
        raw = (
            b'[{"ITEM_SEQ":"one","ITEM_SEQ":"two"}]' if damage == "duplicate-json-key" else kwargs["detail_json"] + b" "
        )
        kwargs["detail_json"] = raw
        if damage != "checksum":
            kwargs["detail_receipt"] = replace(
                kwargs["detail_receipt"], canonical_checksum=hashlib.sha256(raw).hexdigest()
            )
    with pytest.raises(ValueError):
        await load_mfds_catalog(**kwargs)
    kwargs["repository"].save_build.assert_not_awaited()
    kwargs["approval_verifier"].verify.assert_not_awaited()


@pytest.mark.parametrize("rows", [[], [dict(record(), QNT=None)], [record(), dict(record(), QNT="020.00")]])
async def test_incomplete_or_conflicting_whole_input_returns_report_without_partial_catalog(rows):
    kwargs = inputs()
    raw = _canonical_json_bytes(rows)
    kwargs.update(
        detail_json=raw,
        detail_receipt=replace(kwargs["detail_receipt"], canonical_checksum=hashlib.sha256(raw).hexdigest()),
    )
    result = await load_mfds_catalog(**kwargs)
    assert result.build is None
    assert result.inspection.input_count == len(rows)
    assert not result.inspection.eligible_for_mapping
    kwargs["repository"].save_build.assert_not_awaited()
    kwargs["approval_verifier"].verify.assert_not_awaited()


@pytest.mark.parametrize("key", [None, "invented-key"])
def test_observation_requires_key_bound_to_original_fields(key):
    from ai_worker.tasks.rag.catalog.build import build_catalog_members
    from ai_worker.tasks.rag.catalog.mfds_component import map_mfds_component
    from ai_worker.tasks.rag.catalog.types import CatalogComponentObservation

    kwargs = inputs()
    product = kwargs["products"][0]
    ingredient = kwargs["ingredients_by_material"]["synthetic-material"]
    row = record()
    component = map_mfds_component(
        row, ingredient=ingredient, expected_material_code="synthetic-material", component_order=1
    )
    observation = CatalogComponentObservation(
        product.source_snapshot_id,
        ingredient.source_snapshot_id,
        row["ITEM_SEQ"],
        row["TAMT_SEQ"],
        row["MTRAL_SN"],
        row["MTRAL_CODE"],
        row["QNT"],
        row["INGD_UNIT_CD"],
        "synthetic-explicit-v1",
        kwargs["detail_receipt"].canonical_checksum,
        DETAIL_CANONICALIZATION_SPEC,
    )
    with pytest.raises(ValueError, match="original fields"):
        build_catalog_members(
            products=(product,),
            ingredients=(ingredient,),
            aliases=(),
            components=(replace(component, source_record_key=key, observation=observation),),
        )
