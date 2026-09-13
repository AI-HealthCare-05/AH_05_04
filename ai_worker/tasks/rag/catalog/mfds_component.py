"""MFDS 상세 행 변환. 호출자는 승인된 원료 매핑과 명시적인 순서를 제공한다."""

import json
from collections.abc import Mapping

from ai_worker.tasks.rag.catalog.build import CatalogComponentInput, CatalogIngredientInput
from ai_worker.tasks.rag.catalog.normalize import require_official_identity_text
from ai_worker.tasks.rag.catalog.types import CatalogComponentRole


def map_mfds_component(
    record: Mapping[str, object],
    *,
    ingredient: CatalogIngredientInput,
    expected_material_code: str,
    component_order: int,
) -> CatalogComponentInput:
    """누락 키·매핑 불일치를 거부하며 문자열 순번과 분량을 변환하지 않는다.

    이 함수는 매핑 승인 또는 Source Receipt 검증을 대신하지 않는다.
    ingredient의 Snapshot은 해당 상세 행을 보존한 검증된 Snapshot이어야 한다.
    """
    fields = {
        name: require_official_identity_text(record.get(name), field_name=f"mfds_component.{name}")
        for name in ("ITEM_SEQ", "TAMT_SEQ", "MTRAL_SN", "MTRAL_CODE", "QNT", "INGD_UNIT_CD")
    }
    if fields["MTRAL_CODE"] != expected_material_code:
        raise ValueError("MFDS component material mapping mismatch")
    if type(component_order) is not int or component_order < 1:
        raise ValueError("MFDS component requires an explicit positive order")
    key = json.dumps(
        ["mfds-component-key-v1", fields["ITEM_SEQ"], fields["TAMT_SEQ"], fields["MTRAL_SN"]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return CatalogComponentInput(
        source_snapshot_id=ingredient.source_snapshot_id,
        product_code_system="MFDS_ITEM_SEQ",
        product_canonical_code=fields["ITEM_SEQ"],
        ingredient_code_system=ingredient.code_system,
        ingredient_canonical_code=ingredient.canonical_code,
        component_role=CatalogComponentRole.ACTIVE_INGREDIENT,
        component_order=component_order,
        strength_value=fields["QNT"],
        strength_unit=fields["INGD_UNIT_CD"],
        source_record_key=key,
    )
