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
    is_p0_code_system,
)

_REF_SPEC_VERSION = "catalog-member-ref-v1"


def _is_excluded_code_system(entity_type: CandidateEntityType, value: object) -> bool:
    # Malformed non-string values still reach the normal mapping error boundary.
    return isinstance(value, str) and bool(value.strip()) and not is_p0_code_system(entity_type, value)


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
class CatalogIngredientInput:
    source_snapshot_id: str
    source_record_key: str
    code_system: str
    canonical_code: str
    ingredient_name: str
    status: CandidateRecordStatus = CandidateRecordStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class CatalogComponentInput:
    source_snapshot_id: str
    product_code_system: str
    product_canonical_code: str
    ingredient_code_system: str
    ingredient_canonical_code: str
    component_role: CatalogComponentRole
    component_order: int
    strength_value: str
    strength_unit: str
    release_profile: str | None = None
    source_record_key: str | None = None


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


def _raw_optional(value: object | None, *, field_name: str) -> str | None:
    normalized = normalize_optional_catalog_text(value, field_name=field_name)
    return normalized.raw_value if normalized is not None else None


def _product(input_record: CatalogProductInput) -> CatalogProduct:
    snapshot_id = require_official_identity_text(
        input_record.source_snapshot_id,
        field_name="source_snapshot_id",
    )
    # Source 레코드 키는 provenance 입력으로 검증하지만 공식 제품 식별자에는 포함하지 않습니다.
    source_record_key = require_official_identity_text(
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
        source_record_key=source_record_key,
        identity=identity,
        product_name=product_name.raw_value,
        normalized_product_name=product_name.normalized_value,
        strength_text=_raw_optional(
            input_record.strength_text,
            field_name="strength_text",
        ),
        dosage_form=_raw_optional(
            input_record.dosage_form,
            field_name="dosage_form",
        ),
        manufacturer_name=_raw_optional(
            input_record.manufacturer_name,
            field_name="manufacturer_name",
        ),
        source_snapshot_id=snapshot_id,
        normalization_version=CATALOG_NORMALIZATION_VERSION,
        status=input_record.product_status,
    )


def _ingredient(input_record: CatalogIngredientInput) -> CatalogIngredient:
    source_record_key = require_official_identity_text(
        input_record.source_record_key,
        field_name="source_record_key",
    )
    snapshot_id = require_official_identity_text(
        input_record.source_snapshot_id,
        field_name="source_snapshot_id",
    )
    identity = _identity(
        entity_type=CandidateEntityType.INGREDIENT,
        code_system=input_record.code_system,
        canonical_code=input_record.canonical_code,
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
        source_record_key=source_record_key,
        identity=identity,
        ingredient_name=ingredient_name.raw_value,
        normalized_ingredient_name=ingredient_name.normalized_value,
        source_snapshot_id=snapshot_id,
        normalization_version=CATALOG_NORMALIZATION_VERSION,
        status=input_record.status,
    )


def _append_exact_deduplicated[T](items: dict[T, None], item: T) -> None:
    # 행 전체의 동등성과 최초 등장 순서를 유지하며 Identity가 같은 충돌 행도 보존합니다.
    items.setdefault(item, None)


def _append_search_entry(
    entries: list[CatalogSearchEntry],
    seen: set[CatalogSearchEntry],
    entry: CatalogSearchEntry,
) -> None:
    if entry not in seen:
        seen.add(entry)
        entries.append(entry)


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
            }
            if input_record.source_record_key is None
            else {
                "reference_spec": "catalog-component-source-key-v1",
                "product_ref": product.product_ref,
                "source_record_key": require_official_identity_text(
                    input_record.source_record_key, field_name="components.source_record_key"
                ),
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
        alias_text=alias_text.raw_value,
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


def _product_registry(
    products: tuple[CatalogProductInput, ...],
) -> tuple[dict[CatalogProduct, None], dict[tuple[str, ProductIdentity], CatalogProduct]]:
    catalog_products: dict[CatalogProduct, None] = {}
    product_by_identity: dict[tuple[str, ProductIdentity], CatalogProduct] = {}
    for product_input in products:
        if _is_excluded_code_system(CandidateEntityType.PRODUCT, product_input.code_system):
            continue
        catalog_product = _product(product_input)
        _append_exact_deduplicated(catalog_products, catalog_product)
        product_by_identity.setdefault(
            (catalog_product.source_snapshot_id, catalog_product.identity),
            catalog_product,
        )

    return catalog_products, product_by_identity


def _ingredient_registry(
    ingredients: tuple[CatalogIngredientInput, ...],
) -> tuple[dict[CatalogIngredient, None], dict[tuple[str, ProductIdentity], CatalogIngredient]]:
    catalog_ingredients: dict[CatalogIngredient, None] = {}
    ingredient_by_identity: dict[tuple[str, ProductIdentity], CatalogIngredient] = {}
    for ingredient_input in ingredients:
        if _is_excluded_code_system(CandidateEntityType.INGREDIENT, ingredient_input.code_system):
            continue
        ingredient = _ingredient(ingredient_input)
        _append_exact_deduplicated(catalog_ingredients, ingredient)
        ingredient_by_identity.setdefault((ingredient.source_snapshot_id, ingredient.identity), ingredient)
    return catalog_ingredients, ingredient_by_identity


def build_catalog_members(
    *,
    products: tuple[CatalogProductInput, ...],
    ingredients: tuple[CatalogIngredientInput, ...] = (),
    components: tuple[CatalogComponentInput, ...],
    aliases: tuple[CatalogAliasInput, ...],
) -> CatalogMembers:
    """입력 순서를 보존하면서 정확히 같은 행만 중복 제거해 Catalog 구성원을 만듭니다."""

    catalog_products, product_by_identity = _product_registry(products)
    catalog_ingredients, ingredient_by_identity = _ingredient_registry(ingredients)
    catalog_components: dict[CatalogComponent, None] = {}
    for component_input in components:
        if _is_excluded_code_system(
            CandidateEntityType.PRODUCT, component_input.product_code_system
        ) or _is_excluded_code_system(CandidateEntityType.INGREDIENT, component_input.ingredient_code_system):
            continue
        product_identity = _identity(
            entity_type=CandidateEntityType.PRODUCT,
            code_system=component_input.product_code_system,
            canonical_code=component_input.product_canonical_code,
        )
        component_product = product_by_identity.get((component_input.source_snapshot_id, product_identity))
        if component_product is None:
            raise CatalogMappingError("COMPONENT_PRODUCT_NOT_FOUND", ("components.product_identity",))
        ingredient_identity = _identity(
            entity_type=CandidateEntityType.INGREDIENT,
            code_system=component_input.ingredient_code_system,
            canonical_code=component_input.ingredient_canonical_code,
        )
        component_ingredient = ingredient_by_identity.get((component_input.source_snapshot_id, ingredient_identity))
        if component_ingredient is None:
            raise CatalogMappingError("COMPONENT_INGREDIENT_NOT_FOUND", ("components.ingredient_identity",))
        _append_exact_deduplicated(
            catalog_components,
            _component(
                component_input,
                product=component_product,
                ingredient=component_ingredient,
            ),
        )

    search_entries = [
        _product_entry(product) for product in catalog_products if product.status is CandidateRecordStatus.ACTIVE
    ]
    seen_search_entries = set(search_entries)
    catalog_aliases: dict[CatalogAlias, None] = {}
    alias_entries: dict[tuple[str, str], CatalogSearchEntry] = {}
    for alias_input in aliases:
        if _is_excluded_code_system(alias_input.target_type, alias_input.target_code_system):
            continue
        target_identity = _identity(
            entity_type=alias_input.target_type,
            code_system=alias_input.target_code_system,
            canonical_code=alias_input.target_canonical_code,
        )
        target_snapshot_id = require_official_identity_text(
            (
                alias_input.source_snapshot_id
                if alias_input.target_source_snapshot_id is None
                else alias_input.target_source_snapshot_id
            ),
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
            entry = _alias_entry(alias, product=alias_product)
            key = (entry.product_ref, entry.normalized_text)
            alias_entries[key] = min(entry, alias_entries.get(key, entry), key=lambda item: item.alias_ref or "")

    for key in sorted(alias_entries):
        _append_search_entry(search_entries, seen_search_entries, alias_entries[key])

    return CatalogMembers(
        products=tuple(catalog_products),
        ingredients=tuple(catalog_ingredients),
        components=tuple(catalog_components),
        aliases=tuple(catalog_aliases),
        search_entries=tuple(search_entries),
    )
