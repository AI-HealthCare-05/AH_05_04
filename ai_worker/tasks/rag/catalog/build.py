"""검증된 Snapshot 입력을 공식 의약품 Catalog 구성원으로 변환합니다."""

import hashlib
import json
from dataclasses import dataclass

from ai_worker.tasks.rag.catalog.normalize import (
    CATALOG_NORMALIZATION_VERSION,
    normalize_catalog_text,
    normalize_optional_catalog_text,
    require_official_identity_text,
)
from ai_worker.tasks.rag.catalog.types import (
    CandidateAliasReviewStatus,
    CandidateEntityType,
    CandidateEntryType,
    CandidateRecordStatus,
    CatalogAlias,
    CatalogComponent,
    CatalogComponentRole,
    CatalogIngredient,
    CatalogProduct,
    CatalogSearchEntry,
    ProductIdentity,
)

_REF_SPEC_VERSION = "catalog-member-ref-v1"


@dataclass(frozen=True, slots=True)
class CatalogProductInput:
    source_snapshot_id: str
    source_record_key: str
    code_system: str
    canonical_code: str
    product_name: str
    product_status: CandidateRecordStatus
    strength_text: str | None = None
    dosage_form: str | None = None
    manufacturer_name: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogComponentInput:
    source_snapshot_id: str
    product_code_system: str
    product_canonical_code: str
    ingredient_code_system: str
    ingredient_canonical_code: str
    ingredient_name: str
    component_role: CatalogComponentRole
    component_order: int
    strength_value: str
    strength_unit: str
    release_profile: str | None = None
    ingredient_status: CandidateRecordStatus = CandidateRecordStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class CatalogAliasInput:
    source_snapshot_id: str
    source_alias_ref: str
    target_type: CandidateEntityType
    target_code_system: str
    target_canonical_code: str
    alias_source: str
    alias_text: str
    review_status: CandidateAliasReviewStatus
    status: CandidateRecordStatus
    is_effective: bool
    target_source_snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogMembers:
    products: tuple[CatalogProduct, ...]
    ingredients: tuple[CatalogIngredient, ...]
    components: tuple[CatalogComponent, ...]
    aliases: tuple[CatalogAlias, ...]
    search_entries: tuple[CatalogSearchEntry, ...]


class CatalogMappingError(ValueError):
    def __init__(self, code: str, paths: tuple[str, ...]) -> None:
        self.code = code
        self.paths = paths
        super().__init__(code)


def _stable_ref(prefix: str, values: dict[str, object]) -> str:
    payload = {
        "spec_version": _REF_SPEC_VERSION,
        **values,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(serialized).hexdigest()}"


def _identity(
    *,
    entity_type: CandidateEntityType,
    code_system: object,
    canonical_code: object,
) -> ProductIdentity:
    return ProductIdentity(
        entity_type=entity_type,
        code_system=require_official_identity_text(code_system, field_name="code_system"),
        canonical_code=require_official_identity_text(canonical_code, field_name="canonical_code"),
    )


def _identity_values(identity: ProductIdentity) -> dict[str, str]:
    return {
        "entity_type": identity.entity_type.value,
        "code_system": identity.code_system,
        "canonical_code": identity.canonical_code,
    }


def _normalized_optional(value: object | None, *, field_name: str) -> str | None:
    normalized = normalize_optional_catalog_text(value, field_name=field_name)
    return normalized.normalized_value if normalized is not None else None


def _product(input_record: CatalogProductInput) -> CatalogProduct:
    snapshot_id = require_official_identity_text(
        input_record.source_snapshot_id,
        field_name="source_snapshot_id",
    )
    # Source 레코드 키는 provenance 입력으로 검증하지만 공식 제품 식별자에는 포함하지 않습니다.
    require_official_identity_text(
        input_record.source_record_key,
        field_name="source_record_key",
    )
    identity = _identity(
        entity_type=CandidateEntityType.PRODUCT,
        code_system=input_record.code_system,
        canonical_code=input_record.canonical_code,
    )
    product_name = normalize_catalog_text(input_record.product_name, field_name="product_name")
    return CatalogProduct(
        product_ref=_stable_ref(
            "product",
            {
                "source_snapshot_id": snapshot_id,
                "identity": _identity_values(identity),
            },
        ),
        identity=identity,
        product_name=product_name.normalized_value,
        normalized_product_name=product_name.normalized_value,
        strength_text=_normalized_optional(input_record.strength_text, field_name="strength_text"),
        dosage_form=_normalized_optional(input_record.dosage_form, field_name="dosage_form"),
        manufacturer_name=_normalized_optional(input_record.manufacturer_name, field_name="manufacturer_name"),
        source_snapshot_id=snapshot_id,
        normalization_version=CATALOG_NORMALIZATION_VERSION,
        status=input_record.product_status,
    )


def _ingredient(input_record: CatalogComponentInput) -> CatalogIngredient:
    snapshot_id = require_official_identity_text(
        input_record.source_snapshot_id,
        field_name="source_snapshot_id",
    )
    identity = _identity(
        entity_type=CandidateEntityType.INGREDIENT,
        code_system=input_record.ingredient_code_system,
        canonical_code=input_record.ingredient_canonical_code,
    )
    ingredient_name = normalize_catalog_text(input_record.ingredient_name, field_name="ingredient_name")
    return CatalogIngredient(
        ingredient_ref=_stable_ref(
            "ingredient",
            {
                "source_snapshot_id": snapshot_id,
                "identity": _identity_values(identity),
            },
        ),
        identity=identity,
        ingredient_name=ingredient_name.normalized_value,
        normalized_ingredient_name=ingredient_name.normalized_value,
        source_snapshot_id=snapshot_id,
        normalization_version=CATALOG_NORMALIZATION_VERSION,
        status=input_record.ingredient_status,
    )


def _append_exact_deduplicated[T](items: list[T], item: T) -> None:
    if item not in items:
        items.append(item)


def _product_entry(product: CatalogProduct) -> CatalogSearchEntry:
    return CatalogSearchEntry(
        entry_ref=_stable_ref(
            "search-entry",
            {
                "entry_type": CandidateEntryType.PRODUCT_NAME.value,
                "product_ref": product.product_ref,
            },
        ),
        product_ref=product.product_ref,
        identity=product.identity,
        entry_type=CandidateEntryType.PRODUCT_NAME,
        alias_ref=None,
        display_text=product.product_name,
        normalized_text=product.normalized_product_name,
        source_snapshot_id=product.source_snapshot_id,
        normalization_version=product.normalization_version,
        review_status=CandidateAliasReviewStatus.APPROVED,
        status=product.status,
    )


def _component(
    input_record: CatalogComponentInput,
    *,
    product: CatalogProduct,
    ingredient: CatalogIngredient,
) -> CatalogComponent:
    if type(input_record.component_order) is not int or input_record.component_order < 1:
        raise CatalogMappingError("COMPONENT_ORDER_INVALID", ("components.component_order",))
    strength_value = normalize_catalog_text(input_record.strength_value, field_name="strength_value")
    strength_unit = normalize_catalog_text(input_record.strength_unit, field_name="strength_unit")
    release_profile = _normalized_optional(input_record.release_profile, field_name="release_profile")
    return CatalogComponent(
        component_ref=_stable_ref(
            "component",
            {
                "product_ref": product.product_ref,
                "ingredient_ref": ingredient.ingredient_ref,
                "component_role": input_record.component_role.value,
            },
        ),
        product_ref=product.product_ref,
        ingredient_ref=ingredient.ingredient_ref,
        component_order=input_record.component_order,
        strength_value=strength_value.normalized_value,
        strength_unit=strength_unit.normalized_value,
        source_snapshot_id=product.source_snapshot_id,
        component_role=input_record.component_role,
        release_profile=release_profile,
    )


def _alias(
    input_record: CatalogAliasInput,
    *,
    identity: ProductIdentity,
) -> CatalogAlias:
    snapshot_id = require_official_identity_text(
        input_record.source_snapshot_id,
        field_name="source_snapshot_id",
    )
    alias_source = require_official_identity_text(input_record.alias_source, field_name="alias_source")
    source_alias_ref = require_official_identity_text(
        input_record.source_alias_ref,
        field_name="source_alias_ref",
    )
    alias_text = normalize_catalog_text(input_record.alias_text, field_name="alias_text")
    return CatalogAlias(
        alias_ref=_stable_ref(
            "alias",
            {
                "source_snapshot_id": snapshot_id,
                "source_alias_ref": source_alias_ref,
                "alias_source": alias_source,
                "identity": _identity_values(identity),
                "normalized_alias": alias_text.normalized_value,
            },
        ),
        identity=identity,
        alias_text=alias_text.normalized_value,
        normalized_alias=alias_text.normalized_value,
        source_snapshot_id=snapshot_id,
        normalization_version=CATALOG_NORMALIZATION_VERSION,
        review_status=input_record.review_status,
        status=input_record.status,
        is_effective=input_record.is_effective,
        alias_source=alias_source,
    )


def _alias_entry(alias: CatalogAlias, *, product: CatalogProduct) -> CatalogSearchEntry:
    return CatalogSearchEntry(
        entry_ref=_stable_ref(
            "search-entry",
            {
                "entry_type": CandidateEntryType.APPROVED_ALIAS.value,
                "product_ref": product.product_ref,
                "alias_ref": alias.alias_ref,
            },
        ),
        product_ref=product.product_ref,
        identity=product.identity,
        entry_type=CandidateEntryType.APPROVED_ALIAS,
        alias_ref=alias.alias_ref,
        display_text=alias.alias_text,
        normalized_text=alias.normalized_alias,
        source_snapshot_id=alias.source_snapshot_id,
        normalization_version=alias.normalization_version,
        review_status=alias.review_status,
        status=alias.status,
    )


def build_catalog_members(
    *,
    products: tuple[CatalogProductInput, ...],
    components: tuple[CatalogComponentInput, ...],
    aliases: tuple[CatalogAliasInput, ...],
) -> CatalogMembers:
    """입력 순서를 보존하면서 정확히 같은 행만 중복 제거해 Catalog 구성원을 만듭니다."""

    catalog_products: list[CatalogProduct] = []
    product_by_identity: dict[tuple[str, ProductIdentity], CatalogProduct] = {}
    for product_input in products:
        catalog_product = _product(product_input)
        _append_exact_deduplicated(catalog_products, catalog_product)
        product_by_identity.setdefault(
            (catalog_product.source_snapshot_id, catalog_product.identity),
            catalog_product,
        )

    catalog_ingredients: list[CatalogIngredient] = []
    catalog_components: list[CatalogComponent] = []
    ingredient_by_identity: dict[tuple[str, ProductIdentity], CatalogIngredient] = {}
    for component_input in components:
        product_identity = _identity(
            entity_type=CandidateEntityType.PRODUCT,
            code_system=component_input.product_code_system,
            canonical_code=component_input.product_canonical_code,
        )
        component_product = product_by_identity.get((component_input.source_snapshot_id, product_identity))
        if component_product is None:
            raise CatalogMappingError("COMPONENT_PRODUCT_NOT_FOUND", ("components.product_identity",))
        component_ingredient = _ingredient(component_input)
        _append_exact_deduplicated(catalog_ingredients, component_ingredient)
        ingredient_by_identity.setdefault(
            (component_ingredient.source_snapshot_id, component_ingredient.identity),
            component_ingredient,
        )
        _append_exact_deduplicated(
            catalog_components,
            _component(
                component_input,
                product=component_product,
                ingredient=component_ingredient,
            ),
        )

    search_entries = [_product_entry(product) for product in catalog_products]
    catalog_aliases: list[CatalogAlias] = []
    for alias_input in aliases:
        target_identity = _identity(
            entity_type=alias_input.target_type,
            code_system=alias_input.target_code_system,
            canonical_code=alias_input.target_canonical_code,
        )
        target_snapshot_id = require_official_identity_text(
            alias_input.target_source_snapshot_id or alias_input.source_snapshot_id,
            field_name="target_source_snapshot_id",
        )
        target_key = (target_snapshot_id, target_identity)
        alias_product = product_by_identity.get(target_key)
        alias_ingredient = ingredient_by_identity.get(target_key)
        if alias_product is None and alias_ingredient is None:
            raise CatalogMappingError("ALIAS_TARGET_NOT_FOUND", ("aliases.target_identity",))
        alias = _alias(alias_input, identity=target_identity)
        _append_exact_deduplicated(catalog_aliases, alias)
        if (
            alias_product is not None
            and alias_product.status is CandidateRecordStatus.ACTIVE
            and alias.review_status is CandidateAliasReviewStatus.APPROVED
            and alias.status is CandidateRecordStatus.ACTIVE
            and alias.is_effective
        ):
            _append_exact_deduplicated(
                search_entries,
                _alias_entry(alias, product=alias_product),
            )

    return CatalogMembers(
        products=tuple(catalog_products),
        ingredients=tuple(catalog_ingredients),
        components=tuple(catalog_components),
        aliases=tuple(catalog_aliases),
        search_entries=tuple(search_entries),
    )
