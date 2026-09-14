"""MFDS 상세 Snapshot의 검증된 JSON을 Catalog build 경계로 연결한다."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from ai_worker.tasks.rag.catalog.approval import CatalogApprovalVerifier
from ai_worker.tasks.rag.catalog.build import CatalogIngredientInput, CatalogProductInput
from ai_worker.tasks.rag.catalog.component_observation import DETAIL_CANONICALIZATION_SPEC
from ai_worker.tasks.rag.catalog.export import _canonical_json_bytes, _unique_manifest_object
from ai_worker.tasks.rag.catalog.mfds_component import (
    MfdsComponentInspection,
    inspect_mfds_component_rows,
    map_mfds_component,
)
from ai_worker.tasks.rag.catalog.normalize import require_official_identity_text
from ai_worker.tasks.rag.catalog.service import (
    CatalogBuildRepository,
    CatalogBuildRequest,
    CatalogBuildResult,
    build_catalog_candidate,
)
from ai_worker.tasks.rag.catalog.types import CandidateCatalogSourceRef, CatalogComponentObservation
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotProvenanceReceipt


@dataclass(frozen=True, slots=True)
class MfdsCatalogLoadResult:
    inspection: MfdsComponentInspection
    build: CatalogBuildResult | None


async def load_mfds_catalog(
    *,
    catalog_version: str,
    detail_receipt: SnapshotProvenanceReceipt,
    detail_json: bytes,
    product_receipt: SnapshotProvenanceReceipt,
    products: tuple[CatalogProductInput, ...],
    ingredients_by_material: Mapping[str, CatalogIngredientInput],
    orders_by_observation: Mapping[str, int],
    order_spec_version: str,
    repository: CatalogBuildRepository,
    approval_verifier: CatalogApprovalVerifier,
) -> MfdsCatalogLoadResult:
    """공식 순서·원료 매핑은 호출자가 제공한다. API acquisition·승인은 수행하지 않는다.

    detail_json은 해당 Receipt의 canonical artifact이며 배열 전체를 검사한다.
    원문 무결성 위반이 있으면 부분 build를 만들지 않고 원본 값과 사유를 반환한다.
    빈 주성분 행은 구성원을 만들지 않고 제외로 남기며 나머지 행의 build는 계속 진행한다.
    실제 DB adapter는 transaction 안에서 두 Snapshot Receipt를 다시 검증한다.
    """
    rows = _detail_rows(detail_receipt, product_receipt, detail_json, order_spec_version)
    inspection = inspect_mfds_component_rows(tuple(rows))
    if not inspection.eligible_for_mapping:
        return MfdsCatalogLoadResult(inspection, None)
    detail_id = str(detail_receipt.source_snapshot_id)
    product_id = str(product_receipt.source_snapshot_id)
    if not products or any(p.source_snapshot_id != product_id or p.code_system != "MFDS_ITEM_SEQ" for p in products):
        raise ValueError("MFDS product Snapshot binding mismatch")
    # 관찰 행 없는 제품은 빈 주성분 행만 있는 경우이므로 허용한다. 제품은 Catalog와 검색에
    # 남고 구성원만 0개가 된다. 반대로 제품 없는 관찰 행은 orphan Component이므로 계속 거부한다.
    if not {row.item_seq for row in inspection.observations} <= {p.canonical_code for p in products}:
        raise ValueError("MFDS Loader observation scope must stay within the product input")
    if set(orders_by_observation) != {row.source_record_key for row in inspection.observations}:
        raise ValueError("MFDS Loader requires exact explicit observation orders")
    if set(ingredients_by_material) != {row.material_code for row in inspection.observations}:
        raise ValueError("MFDS Loader requires exact material mapping scope")
    components = []
    for row in inspection.observations:
        ingredient = ingredients_by_material[row.material_code]
        if (
            ingredient.source_snapshot_id != detail_id
            or ingredient.code_system != "MFDS_INGREDIENT_CODE"
            or ingredient.canonical_code != row.material_code
        ):
            raise ValueError("MFDS Ingredient must retain the source-local material code")
        component = map_mfds_component(
            json.loads(row.record_json),
            ingredient=ingredient,
            expected_material_code=row.material_code,
            component_order=orders_by_observation[row.source_record_key],
        )
        components.append(
            replace(
                component,
                observation=CatalogComponentObservation(
                    product_id,
                    detail_id,
                    row.item_seq,
                    row.tamt_seq,
                    row.mtral_sn,
                    row.material_code,
                    row.quantity,
                    row.unit,
                    order_spec_version,
                    detail_receipt.canonical_checksum,
                    detail_receipt.canonicalization_spec_version,
                ),
            )
        )
    refs = tuple(
        sorted(
            {
                CandidateCatalogSourceRef(str(r.source_snapshot_id), r.source_version)
                for r in (product_receipt, detail_receipt)
            },
            key=lambda r: r.snapshot_id,
        )
    )
    request = CatalogBuildRequest(
        catalog_version=catalog_version,
        source_refs=refs,
        products=products,
        ingredients=tuple(ingredients_by_material.values()),
        components=tuple(components),
        aliases=(),
    )
    result = await build_catalog_candidate(request=request, repository=repository, approval_verifier=approval_verifier)
    return MfdsCatalogLoadResult(inspection, result)


def _detail_rows(
    detail_receipt: SnapshotProvenanceReceipt,
    product_receipt: SnapshotProvenanceReceipt,
    detail_json: bytes,
    order_spec_version: str,
) -> list[dict[str, object]]:
    for receipt in (detail_receipt, product_receipt):
        receipt.validate_provenance()
        if receipt.rejected_record_count:
            raise ValueError("MFDS Loader requires unrejected Snapshot input")
    if detail_receipt.canonicalization_spec_version != DETAIL_CANONICALIZATION_SPEC:
        raise ValueError("MFDS detail canonicalization contract mismatch")
    if hashlib.sha256(detail_json).hexdigest() != detail_receipt.canonical_checksum:
        raise ValueError("MFDS detail artifact checksum mismatch")
    require_official_identity_text(order_spec_version, field_name="order_spec_version")
    try:
        rows = json.loads(detail_json, object_pairs_hook=_unique_manifest_object)
    except (ValueError, UnicodeError):
        raise ValueError("MFDS detail artifact is invalid") from None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("MFDS detail artifact must contain record objects")
    if _canonical_json_bytes(rows) != detail_json:
        raise ValueError("MFDS detail artifact is not canonical JSON")
    return rows
