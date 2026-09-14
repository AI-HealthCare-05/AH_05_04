"""Catalog 생산·저장·Candidate 소비가 공유하는 Component 출처 검사."""

import re
from dataclasses import fields

from ai_worker.tasks.rag.catalog.normalize import require_official_identity_text
from ai_worker.tasks.rag.catalog.types import (
    CatalogComponent,
    CatalogComponentObservation,
    CatalogIngredient,
    CatalogProduct,
)

DETAIL_CANONICALIZATION_SPEC = "mfds-component-observations-v1"


def component_sources_are_valid(
    component: CatalogComponent, product: CatalogProduct | None, ingredient: CatalogIngredient | None
) -> bool:
    if product is None or ingredient is None:
        return False
    observation = component.observation
    if observation is None:
        return component.source_snapshot_id == product.source_snapshot_id == ingredient.source_snapshot_id
    if not isinstance(observation, CatalogComponentObservation):
        return False
    try:
        for item in fields(observation):
            require_official_identity_text(getattr(observation, item.name), field_name="component_observation")
    except ValueError:
        return False
    return (
        re.fullmatch(r"[0-9a-f]{64}", observation.source_canonical_checksum) is not None
        and observation.source_canonicalization_spec_version == DETAIL_CANONICALIZATION_SPEC
        and observation.product_source_snapshot_id == product.source_snapshot_id
        and observation.ingredient_source_snapshot_id == ingredient.source_snapshot_id == component.source_snapshot_id
        and observation.item_seq == product.identity.canonical_code
        and product.identity.code_system == "MFDS_ITEM_SEQ"
        and observation.material_code == ingredient.identity.canonical_code
        and ingredient.identity.code_system == "MFDS_INGREDIENT_CODE"
        and observation.quantity == component.strength_value
        and observation.unit == component.strength_unit
    )
