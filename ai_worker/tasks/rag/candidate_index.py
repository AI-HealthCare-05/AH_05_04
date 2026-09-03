"""Pure RAG-07A medication Candidate Index contracts and deterministic logic."""

import dataclasses
import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class CandidateEntityType(StrEnum):
    PRODUCT = "PRODUCT"
    INGREDIENT = "INGREDIENT"


class CandidateEntryType(StrEnum):
    PRODUCT_NAME = "PRODUCT_NAME"
    APPROVED_ALIAS = "APPROVED_ALIAS"


class CandidateRecordStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class CandidateAliasReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CatalogVerificationStatus(StrEnum):
    APPROVED = "APPROVED"
    NOT_APPROVED = "NOT_APPROVED"


class CatalogFreshnessStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class CandidateIndexBuildMode(StrEnum):
    LEXICAL_ONLY = "LEXICAL_ONLY"
    HYBRID = "HYBRID"


class CandidateIndexKind(StrEnum):
    MEDICATION_CANDIDATE = "MEDICATION_CANDIDATE"


class CandidateDistanceMetric(StrEnum):
    COSINE = "COSINE"


class CandidateIndexBuildFailureReason(StrEnum):
    CATALOG_NOT_APPROVED = "CATALOG_NOT_APPROVED"
    CATALOG_STALE = "CATALOG_STALE"
    CATALOG_PARTIAL = "CATALOG_PARTIAL"
    CATALOG_MANIFEST_INVALID = "CATALOG_MANIFEST_INVALID"
    CATALOG_COUNT_MISMATCH = "CATALOG_COUNT_MISMATCH"
    DUPLICATE_PRODUCT_IDENTITY = "DUPLICATE_PRODUCT_IDENTITY"
    REFERENTIAL_INTEGRITY_INVALID = "REFERENTIAL_INTEGRITY_INVALID"
    ALIAS_CONFLICT = "ALIAS_CONFLICT"
    MEMBER_CONFLICT = "MEMBER_CONFLICT"
    BUILD_CONFIG_INVALID = "BUILD_CONFIG_INVALID"
    EMBEDDING_OUTPUT_INVALID = "EMBEDDING_OUTPUT_INVALID"


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    entity_type: CandidateEntityType
    code_system: str
    canonical_code: str


@dataclass(frozen=True, slots=True)
class CatalogProduct:
    product_ref: str
    identity: ProductIdentity
    product_name: str
    normalized_product_name: str
    strength_text: str | None
    dosage_form: str | None
    manufacturer_name: str | None
    source_snapshot_id: str
    normalization_version: str
    status: CandidateRecordStatus


@dataclass(frozen=True, slots=True)
class CatalogIngredient:
    ingredient_ref: str
    identity: ProductIdentity
    ingredient_name: str
    normalized_ingredient_name: str
    source_snapshot_id: str
    normalization_version: str
    status: CandidateRecordStatus


@dataclass(frozen=True, slots=True)
class CatalogComponent:
    component_ref: str
    product_ref: str
    ingredient_ref: str
    component_order: int
    strength_value: str
    strength_unit: str
    source_snapshot_id: str


@dataclass(frozen=True, slots=True)
class CatalogAlias:
    alias_ref: str
    identity: ProductIdentity
    alias_text: str
    normalized_alias: str
    source_snapshot_id: str
    normalization_version: str
    review_status: CandidateAliasReviewStatus
    status: CandidateRecordStatus
    is_effective: bool


@dataclass(frozen=True, slots=True)
class CatalogSearchEntry:
    entry_ref: str
    product_ref: str
    identity: ProductIdentity
    entry_type: CandidateEntryType
    alias_ref: str | None
    display_text: str
    normalized_text: str
    source_snapshot_id: str
    normalization_version: str
    review_status: CandidateAliasReviewStatus
    status: CandidateRecordStatus


@dataclass(frozen=True, slots=True)
class CandidateCatalogCounts:
    product_count: int
    ingredient_count: int
    component_count: int
    alias_count: int
    search_entry_count: int


@dataclass(frozen=True, slots=True)
class CandidateCatalogExport:
    catalog_version: str
    catalog_manifest_hash: str
    source_snapshot_ids: tuple[str, ...]
    source_versions: tuple[str, ...]
    schema_version: str
    normalization_version: str
    verification_status: CatalogVerificationStatus
    freshness_status: CatalogFreshnessStatus
    is_complete: bool
    products: tuple[CatalogProduct, ...]
    ingredients: tuple[CatalogIngredient, ...]
    components: tuple[CatalogComponent, ...]
    aliases: tuple[CatalogAlias, ...]
    search_entries: tuple[CatalogSearchEntry, ...]
    declared_counts: CandidateCatalogCounts
    duplicate_identity_count: int
    orphan_count: int
    conflict_count: int


@dataclass(frozen=True, slots=True)
class CandidateIndexBuildConfig:
    index_code: str
    index_version: str
    normalization_version: str
    lexical_config_version: str
    search_order_version: str
    candidate_limit: int
    display_limit: int
    build_mode: CandidateIndexBuildMode
    embedding_provider: str | None
    embedding_model: str | None
    embedding_model_version: str | None
    embedding_dimension: int | None
    distance_metric: CandidateDistanceMetric | None
    ann_config: tuple[tuple[str, str], ...] | None


@dataclass(frozen=True, slots=True)
class CandidateIndexBuildFailure:
    reason: CandidateIndexBuildFailureReason
    details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateIndexMember:
    identity: ProductIdentity
    product_ref: str
    entry_ref: str
    entry_type: CandidateEntryType
    display_text: str
    normalized_text: str
    alias_ref: str | None
    product_name: str
    strength_text: str | None
    dosage_form: str | None
    manufacturer_name: str | None
    source_snapshot_id: str
    catalog_version: str
    catalog_manifest_hash: str
    normalization_version: str
    member_key: str
    member_content_hash: str
    embedding: tuple[float, ...] | None


@dataclass(frozen=True, slots=True)
class CandidateIndexManifest:
    index_kind: CandidateIndexKind
    index_code: str
    index_version: str
    build_mode: CandidateIndexBuildMode
    catalog_version: str
    catalog_manifest_hash: str
    source_snapshot_ids: tuple[str, ...]
    source_versions: tuple[str, ...]
    schema_version: str
    normalization_version: str
    lexical_config_version: str
    search_order_version: str
    candidate_limit: int
    display_limit: int
    embedding_provider: str | None
    embedding_model: str | None
    embedding_model_version: str | None
    embedding_dimension: int | None
    distance_metric: CandidateDistanceMetric | None
    ann_config: tuple[tuple[str, str], ...] | None
    member_count: int
    product_identity_count: int
    product_name_count: int
    approved_alias_count: int
    vector_count: int
    member_set_hash: str
    configuration_hash: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class CandidateIndexBuildSuccess:
    manifest: CandidateIndexManifest
    members: tuple[CandidateIndexMember, ...]


class CandidateSearchStage(StrEnum):
    PRODUCT_NAME_EXACT = "PRODUCT_NAME_EXACT"
    APPROVED_ALIAS_EXACT = "APPROVED_ALIAS_EXACT"
    TRIGRAM_EDIT_DISTANCE = "TRIGRAM_EDIT_DISTANCE"
    DENSE_VECTOR = "DENSE_VECTOR"


class CandidateIndexSearchFailureReason(StrEnum):
    QUERY_INVALID = "QUERY_INVALID"
    INDEX_VERSION_MISMATCH = "INDEX_VERSION_MISMATCH"
    PORT_FAILURE = "PORT_FAILURE"
    HIT_PROVENANCE_MISMATCH = "HIT_PROVENANCE_MISMATCH"


@dataclass(frozen=True, slots=True)
class CandidateSearchQuery:
    index_version: str
    normalized_query: str
    retrieval_limit: int


@dataclass(frozen=True, slots=True)
class CandidateRawHit:
    identity: ProductIdentity
    member_key: str
    stage: CandidateSearchStage
    rank: int
    stage_score: float
    index_version: str
    catalog_version: str
    source_snapshot_id: str
    normalization_version: str
    embedding_model_version: str | None


@dataclass(frozen=True, slots=True)
class CandidateIndexSearchSuccess:
    raw_hits: tuple[CandidateRawHit, ...]


@dataclass(frozen=True, slots=True)
class CandidateIndexSearchFailure:
    reason: CandidateIndexSearchFailureReason
    details: tuple[str, ...]
    raw_hits: tuple[CandidateRawHit, ...] = ()


class CandidateIndexSearchPort(Protocol):
    def search_product_name_exact(
        self,
        query: CandidateSearchQuery,
        manifest: CandidateIndexManifest,
    ) -> tuple[CandidateRawHit, ...]: ...

    def search_approved_alias_exact(
        self,
        query: CandidateSearchQuery,
        manifest: CandidateIndexManifest,
    ) -> tuple[CandidateRawHit, ...]: ...

    def search_trigram_edit_distance(
        self,
        query: CandidateSearchQuery,
        manifest: CandidateIndexManifest,
    ) -> tuple[CandidateRawHit, ...]: ...

    def search_dense_vector(
        self,
        query: CandidateSearchQuery,
        manifest: CandidateIndexManifest,
    ) -> tuple[CandidateRawHit, ...]: ...


@dataclass(frozen=True, slots=True)
class CandidateEmbeddingRequest:
    member_key: str
    normalized_text: str


@dataclass(frozen=True, slots=True)
class CandidateEmbeddingVector:
    member_key: str
    values: tuple[float, ...]


class CandidateEmbeddingPort(Protocol):
    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]: ...


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical_json_bytes(value: object) -> bytes:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return unicodedata.normalize("NFC", serialized).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _identity_key(identity: ProductIdentity) -> str:
    return f"{identity.entity_type.value}:{identity.code_system}:{identity.canonical_code}"


def _stable_text_sort_key(value: str) -> bytes:
    return unicodedata.normalize("NFC", value).encode("utf-8")


def _member_sort_key(member: CandidateIndexMember) -> tuple[bytes, bytes, bytes]:
    return (
        _stable_text_sort_key(_identity_key(member.identity)),
        _stable_text_sort_key(member.entry_type.value),
        _stable_text_sort_key(member.entry_ref),
    )


def _catalog_count_mismatches(catalog: CandidateCatalogExport) -> tuple[str, ...]:
    observed = {
        "product_count": len(catalog.products),
        "ingredient_count": len(catalog.ingredients),
        "component_count": len(catalog.components),
        "alias_count": len(catalog.aliases),
        "search_entry_count": len(catalog.search_entries),
    }
    return tuple(
        field_name
        for field_name, observed_count in observed.items()
        if getattr(catalog.declared_counts, field_name) != observed_count
    )


def _config_is_valid(catalog: CandidateCatalogExport, config: CandidateIndexBuildConfig) -> bool:
    if not all(
        (
            config.index_code,
            config.index_version,
            config.normalization_version,
            config.lexical_config_version,
            config.search_order_version,
        )
    ):
        return False
    if config.normalization_version != catalog.normalization_version:
        return False
    if config.candidate_limit < 1 or config.display_limit != 1 or config.display_limit > config.candidate_limit:
        return False

    embedding_fields = (
        config.embedding_provider,
        config.embedding_model,
        config.embedding_model_version,
        config.embedding_dimension,
        config.distance_metric,
        config.ann_config,
    )
    if config.build_mode is CandidateIndexBuildMode.LEXICAL_ONLY:
        return all(value is None for value in embedding_fields)
    return (
        all(value is not None for value in embedding_fields)
        and config.embedding_dimension is not None
        and config.embedding_dimension > 0
        and config.distance_metric is CandidateDistanceMetric.COSINE
    )


def _product_failure(catalog: CandidateCatalogExport) -> CandidateIndexBuildFailure | None:
    product_by_ref: dict[str, CatalogProduct] = {}
    product_identity_keys: set[str] = set()
    for product in catalog.products:
        identity_key = _identity_key(product.identity)
        if identity_key in product_identity_keys:
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.DUPLICATE_PRODUCT_IDENTITY,
                (identity_key,),
            )
        if (
            not product.product_ref
            or product.product_ref in product_by_ref
            or product.identity.entity_type is not CandidateEntityType.PRODUCT
            or product.source_snapshot_id not in catalog.source_snapshot_ids
            or product.normalization_version != catalog.normalization_version
        ):
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                (product.product_ref,),
            )
        product_identity_keys.add(identity_key)
        product_by_ref[product.product_ref] = product
    return None


def _ingredient_failure(catalog: CandidateCatalogExport) -> CandidateIndexBuildFailure | None:
    ingredient_by_ref: dict[str, CatalogIngredient] = {}
    ingredient_identity_keys: set[str] = set()
    for ingredient in catalog.ingredients:
        identity_key = _identity_key(ingredient.identity)
        if (
            not ingredient.ingredient_ref
            or ingredient.ingredient_ref in ingredient_by_ref
            or identity_key in ingredient_identity_keys
            or ingredient.identity.entity_type is not CandidateEntityType.INGREDIENT
            or ingredient.source_snapshot_id not in catalog.source_snapshot_ids
            or ingredient.normalization_version != catalog.normalization_version
        ):
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                (ingredient.ingredient_ref,),
            )
        ingredient_identity_keys.add(identity_key)
        ingredient_by_ref[ingredient.ingredient_ref] = ingredient
    return None


def _component_failure(catalog: CandidateCatalogExport) -> CandidateIndexBuildFailure | None:
    product_by_ref = {item.product_ref: item for item in catalog.products}
    ingredient_by_ref = {item.ingredient_ref: item for item in catalog.ingredients}
    component_refs: set[str] = set()
    for component in catalog.components:
        component_product = product_by_ref.get(component.product_ref)
        component_ingredient = ingredient_by_ref.get(component.ingredient_ref)
        if (
            not component.component_ref
            or component.component_ref in component_refs
            or component_product is None
            or component_ingredient is None
            or component.component_order < 1
            or component.source_snapshot_id != component_product.source_snapshot_id
            or component.source_snapshot_id != component_ingredient.source_snapshot_id
        ):
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                (component.component_ref,),
            )
        component_refs.add(component.component_ref)
    return None


def _alias_failure(catalog: CandidateCatalogExport) -> CandidateIndexBuildFailure | None:
    product_identity_keys = {_identity_key(item.identity) for item in catalog.products}
    ingredient_identity_keys = {_identity_key(item.identity) for item in catalog.ingredients}
    alias_by_ref: dict[str, CatalogAlias] = {}
    approved_product_alias_targets: dict[str, str] = {}
    all_identity_keys = product_identity_keys | ingredient_identity_keys
    for alias in catalog.aliases:
        identity_key = _identity_key(alias.identity)
        if (
            not alias.alias_ref
            or alias.alias_ref in alias_by_ref
            or identity_key not in all_identity_keys
            or alias.source_snapshot_id not in catalog.source_snapshot_ids
            or alias.normalization_version != catalog.normalization_version
        ):
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                (alias.alias_ref,),
            )
        if (
            alias.identity.entity_type is CandidateEntityType.PRODUCT
            and alias.review_status is CandidateAliasReviewStatus.APPROVED
            and alias.status is CandidateRecordStatus.ACTIVE
            and alias.is_effective
        ):
            existing_target = approved_product_alias_targets.get(alias.normalized_alias)
            if existing_target is not None and existing_target != identity_key:
                return CandidateIndexBuildFailure(
                    CandidateIndexBuildFailureReason.ALIAS_CONFLICT,
                    (alias.normalized_alias,),
                )
            approved_product_alias_targets[alias.normalized_alias] = identity_key
        alias_by_ref[alias.alias_ref] = alias
    return None


def _search_entry_failure(catalog: CandidateCatalogExport) -> CandidateIndexBuildFailure | None:
    product_by_ref = {item.product_ref: item for item in catalog.products}
    alias_by_ref = {item.alias_ref: item for item in catalog.aliases}
    for entry in catalog.search_entries:
        entry_product = product_by_ref.get(entry.product_ref)
        if (
            not entry.entry_ref
            or entry_product is None
            or entry.identity != entry_product.identity
            or entry.identity.entity_type is not CandidateEntityType.PRODUCT
            or entry.source_snapshot_id != entry_product.source_snapshot_id
            or entry.normalization_version != catalog.normalization_version
        ):
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                (entry.entry_ref,),
            )
        if entry.entry_type is CandidateEntryType.PRODUCT_NAME:
            if entry.alias_ref is not None:
                return CandidateIndexBuildFailure(
                    CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                    (entry.entry_ref,),
                )
            continue
        entry_alias = alias_by_ref.get(entry.alias_ref or "")
        if entry_alias is None or entry_alias.identity != entry.identity:
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                (entry.entry_ref,),
            )
        if entry.status is CandidateRecordStatus.ACTIVE and entry.review_status is CandidateAliasReviewStatus.APPROVED:
            if (
                entry_alias.review_status is not CandidateAliasReviewStatus.APPROVED
                or entry_alias.status is not CandidateRecordStatus.ACTIVE
                or not entry_alias.is_effective
                or entry.display_text != entry_alias.alias_text
                or entry.normalized_text != entry_alias.normalized_alias
            ):
                return CandidateIndexBuildFailure(
                    CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
                    (entry.entry_ref,),
                )
    return None


def _catalog_relationship_failure(catalog: CandidateCatalogExport) -> CandidateIndexBuildFailure | None:
    validators = (
        _product_failure,
        _ingredient_failure,
        _component_failure,
        _alias_failure,
        _search_entry_failure,
    )
    for validate in validators:
        failure = validate(catalog)
        if failure is not None:
            return failure
    return None


def _member_payload(
    catalog: CandidateCatalogExport,
    product: CatalogProduct,
    entry: CatalogSearchEntry,
) -> dict[str, object]:
    return {
        "identity": dataclasses.asdict(entry.identity),
        "product_ref": product.product_ref,
        "entry_ref": entry.entry_ref,
        "entry_type": entry.entry_type.value,
        "display_text": entry.display_text,
        "normalized_text": entry.normalized_text,
        "alias_ref": entry.alias_ref,
        "product_name": product.product_name,
        "strength_text": product.strength_text,
        "dosage_form": product.dosage_form,
        "manufacturer_name": product.manufacturer_name,
        "source_snapshot_id": entry.source_snapshot_id,
        "catalog_version": catalog.catalog_version,
        "catalog_manifest_hash": catalog.catalog_manifest_hash,
        "normalization_version": entry.normalization_version,
    }


def _build_lexical_members(
    catalog: CandidateCatalogExport,
) -> CandidateIndexBuildFailure | tuple[CandidateIndexMember, ...]:
    product_by_ref = {product.product_ref: product for product in catalog.products}
    members_by_key: dict[str, CandidateIndexMember] = {}
    for entry in catalog.search_entries:
        if (
            entry.status is not CandidateRecordStatus.ACTIVE
            or entry.review_status is not CandidateAliasReviewStatus.APPROVED
        ):
            continue
        product = product_by_ref[entry.product_ref]
        if product.status is not CandidateRecordStatus.ACTIVE:
            continue
        member_key = _sha256(
            {
                "identity": _identity_key(entry.identity),
                "entry_type": entry.entry_type.value,
                "entry_ref": entry.entry_ref,
            }
        )
        payload = _member_payload(catalog, product, entry)
        member = CandidateIndexMember(
            identity=entry.identity,
            product_ref=product.product_ref,
            entry_ref=entry.entry_ref,
            entry_type=entry.entry_type,
            display_text=entry.display_text,
            normalized_text=entry.normalized_text,
            alias_ref=entry.alias_ref,
            product_name=product.product_name,
            strength_text=product.strength_text,
            dosage_form=product.dosage_form,
            manufacturer_name=product.manufacturer_name,
            source_snapshot_id=entry.source_snapshot_id,
            catalog_version=catalog.catalog_version,
            catalog_manifest_hash=catalog.catalog_manifest_hash,
            normalization_version=entry.normalization_version,
            member_key=member_key,
            member_content_hash=_sha256(payload),
            embedding=None,
        )
        existing = members_by_key.get(member_key)
        if existing is not None and existing != member:
            return CandidateIndexBuildFailure(
                CandidateIndexBuildFailureReason.MEMBER_CONFLICT,
                (entry.entry_ref,),
            )
        members_by_key[member_key] = member
    return tuple(sorted(members_by_key.values(), key=_member_sort_key))


def _embedding_values_are_valid(values: tuple[float, ...], dimension: int) -> bool:
    return len(values) == dimension and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values
    )


def _attach_embeddings(
    members: tuple[CandidateIndexMember, ...],
    config: CandidateIndexBuildConfig,
    embedding_port: CandidateEmbeddingPort | None,
) -> CandidateIndexBuildFailure | tuple[CandidateIndexMember, ...]:
    if embedding_port is None:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
            ("embedding_port",),
        )
    requests = tuple(
        CandidateEmbeddingRequest(member_key=member.member_key, normalized_text=member.normalized_text)
        for member in members
    )
    try:
        vectors = embedding_port.embed(requests, config)
    except Exception:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
            ("embedding_port",),
        )
    expected_keys = tuple(request.member_key for request in requests)
    observed_keys = tuple(vector.member_key for vector in vectors)
    dimension = config.embedding_dimension
    if (
        dimension is None
        or len(vectors) != len(members)
        or observed_keys != expected_keys
        or any(not _embedding_values_are_valid(vector.values, dimension) for vector in vectors)
    ):
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
            ("embedding_output",),
        )
    return tuple(
        dataclasses.replace(
            member,
            embedding=vector.values,
            member_content_hash=_sha256(
                {
                    "lexical_member_content_hash": member.member_content_hash,
                    "embedding_model_version": config.embedding_model_version,
                    "embedding": vector.values,
                }
            ),
        )
        for member, vector in zip(members, vectors, strict=True)
    )


def _configuration_payload(config: CandidateIndexBuildConfig) -> dict[str, object]:
    return {
        "index_code": config.index_code,
        "index_version": config.index_version,
        "normalization_version": config.normalization_version,
        "lexical_config_version": config.lexical_config_version,
        "search_order_version": config.search_order_version,
        "candidate_limit": config.candidate_limit,
        "display_limit": config.display_limit,
        "build_mode": config.build_mode.value,
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "embedding_model_version": config.embedding_model_version,
        "embedding_dimension": config.embedding_dimension,
        "distance_metric": config.distance_metric.value if config.distance_metric is not None else None,
        "ann_config": config.ann_config,
    }


def _build_manifest(
    catalog: CandidateCatalogExport,
    config: CandidateIndexBuildConfig,
    members: tuple[CandidateIndexMember, ...],
) -> CandidateIndexManifest:
    member_set_hash = _sha256(
        [{"member_key": member.member_key, "member_content_hash": member.member_content_hash} for member in members]
    )
    configuration_hash = _sha256(_configuration_payload(config))
    source_snapshot_ids = tuple(sorted(catalog.source_snapshot_ids, key=_stable_text_sort_key))
    source_versions = tuple(sorted(catalog.source_versions, key=_stable_text_sort_key))
    member_count = len(members)
    product_identity_count = len({_identity_key(member.identity) for member in members})
    product_name_count = sum(member.entry_type is CandidateEntryType.PRODUCT_NAME for member in members)
    approved_alias_count = sum(member.entry_type is CandidateEntryType.APPROVED_ALIAS for member in members)
    vector_count = sum(member.embedding is not None for member in members)
    manifest_values: dict[str, object] = {
        "index_kind": CandidateIndexKind.MEDICATION_CANDIDATE.value,
        "index_code": config.index_code,
        "index_version": config.index_version,
        "build_mode": config.build_mode.value,
        "catalog_version": catalog.catalog_version,
        "catalog_manifest_hash": catalog.catalog_manifest_hash,
        "source_snapshot_ids": source_snapshot_ids,
        "source_versions": source_versions,
        "schema_version": catalog.schema_version,
        "normalization_version": config.normalization_version,
        "lexical_config_version": config.lexical_config_version,
        "search_order_version": config.search_order_version,
        "candidate_limit": config.candidate_limit,
        "display_limit": config.display_limit,
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "embedding_model_version": config.embedding_model_version,
        "embedding_dimension": config.embedding_dimension,
        "distance_metric": config.distance_metric.value if config.distance_metric is not None else None,
        "ann_config": config.ann_config,
        "member_count": member_count,
        "product_identity_count": product_identity_count,
        "product_name_count": product_name_count,
        "approved_alias_count": approved_alias_count,
        "vector_count": vector_count,
        "member_set_hash": member_set_hash,
        "configuration_hash": configuration_hash,
    }
    return CandidateIndexManifest(
        index_kind=CandidateIndexKind.MEDICATION_CANDIDATE,
        index_code=config.index_code,
        index_version=config.index_version,
        build_mode=config.build_mode,
        catalog_version=catalog.catalog_version,
        catalog_manifest_hash=catalog.catalog_manifest_hash,
        source_snapshot_ids=source_snapshot_ids,
        source_versions=source_versions,
        schema_version=catalog.schema_version,
        normalization_version=config.normalization_version,
        lexical_config_version=config.lexical_config_version,
        search_order_version=config.search_order_version,
        candidate_limit=config.candidate_limit,
        display_limit=config.display_limit,
        embedding_provider=config.embedding_provider,
        embedding_model=config.embedding_model,
        embedding_model_version=config.embedding_model_version,
        embedding_dimension=config.embedding_dimension,
        distance_metric=config.distance_metric,
        ann_config=config.ann_config,
        member_count=member_count,
        product_identity_count=product_identity_count,
        product_name_count=product_name_count,
        approved_alias_count=approved_alias_count,
        vector_count=vector_count,
        member_set_hash=member_set_hash,
        configuration_hash=configuration_hash,
        content_hash=_sha256(manifest_values),
    )


def _catalog_envelope_failure(catalog: CandidateCatalogExport) -> CandidateIndexBuildFailure | None:
    checks = (
        (
            catalog.verification_status is not CatalogVerificationStatus.APPROVED,
            CandidateIndexBuildFailureReason.CATALOG_NOT_APPROVED,
            "verification_status",
        ),
        (
            catalog.freshness_status is not CatalogFreshnessStatus.CURRENT,
            CandidateIndexBuildFailureReason.CATALOG_STALE,
            "freshness_status",
        ),
        (not catalog.is_complete, CandidateIndexBuildFailureReason.CATALOG_PARTIAL, "is_complete"),
        (
            bool(catalog.duplicate_identity_count),
            CandidateIndexBuildFailureReason.DUPLICATE_PRODUCT_IDENTITY,
            "duplicate_identity_count",
        ),
        (bool(catalog.orphan_count), CandidateIndexBuildFailureReason.CATALOG_PARTIAL, "orphan_count"),
        (bool(catalog.conflict_count), CandidateIndexBuildFailureReason.ALIAS_CONFLICT, "conflict_count"),
        (
            not _is_sha256(catalog.catalog_manifest_hash),
            CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID,
            "catalog_manifest_hash",
        ),
    )
    for failed, reason, detail in checks:
        if failed:
            return CandidateIndexBuildFailure(reason, (detail,))
    count_mismatches = _catalog_count_mismatches(catalog)
    if count_mismatches:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.CATALOG_COUNT_MISMATCH,
            count_mismatches,
        )
    return None


def build_candidate_index(
    catalog: CandidateCatalogExport,
    config: CandidateIndexBuildConfig,
    embedding_port: CandidateEmbeddingPort | None = None,
) -> CandidateIndexBuildSuccess | CandidateIndexBuildFailure:
    """Validate the RAG-06 handoff before any Candidate members are constructed."""

    envelope_failure = _catalog_envelope_failure(catalog)
    if envelope_failure is not None:
        return envelope_failure
    if not _config_is_valid(catalog, config):
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.BUILD_CONFIG_INVALID,
            ("config",),
        )
    relationship_failure = _catalog_relationship_failure(catalog)
    if relationship_failure is not None:
        return relationship_failure
    members = _build_lexical_members(catalog)
    if isinstance(members, CandidateIndexBuildFailure):
        return members
    if config.build_mode is CandidateIndexBuildMode.HYBRID:
        embedded_members = _attach_embeddings(members, config, embedding_port)
        if isinstance(embedded_members, CandidateIndexBuildFailure):
            return embedded_members
        members = embedded_members
    return CandidateIndexBuildSuccess(
        manifest=_build_manifest(catalog, config, members),
        members=members,
    )


def _search_query_failure(
    query: CandidateSearchQuery,
    manifest: CandidateIndexManifest,
) -> CandidateIndexSearchFailure | None:
    normalized_query = unicodedata.normalize("NFC", query.normalized_query)
    if (
        not query.index_version
        or not query.normalized_query
        or query.normalized_query != query.normalized_query.strip()
        or query.normalized_query != normalized_query
        or query.retrieval_limit < 1
        or query.retrieval_limit > manifest.candidate_limit
    ):
        return CandidateIndexSearchFailure(
            CandidateIndexSearchFailureReason.QUERY_INVALID,
            ("query",),
        )
    if query.index_version != manifest.index_version:
        return CandidateIndexSearchFailure(
            CandidateIndexSearchFailureReason.INDEX_VERSION_MISMATCH,
            ("index_version",),
        )
    return None


def _hit_failure_detail(
    hits: tuple[CandidateRawHit, ...],
    expected_stage: CandidateSearchStage,
    query: CandidateSearchQuery,
    manifest: CandidateIndexManifest,
) -> str | None:
    if len(hits) > query.retrieval_limit:
        return "retrieval_limit"
    for expected_rank, hit in enumerate(hits, start=1):
        checks = (
            (hit.stage is expected_stage, "stage"),
            (hit.index_version == manifest.index_version, "index_version"),
            (hit.catalog_version == manifest.catalog_version, "catalog_version"),
            (hit.source_snapshot_id in manifest.source_snapshot_ids, "source_snapshot_id"),
            (hit.normalization_version == manifest.normalization_version, "normalization_version"),
            (hit.identity.entity_type is CandidateEntityType.PRODUCT, "identity"),
            (_is_sha256(hit.member_key), "member_key"),
            (hit.rank == expected_rank, "rank"),
            (
                isinstance(hit.stage_score, (int, float))
                and not isinstance(hit.stage_score, bool)
                and math.isfinite(hit.stage_score),
                "stage_score",
            ),
        )
        for valid, detail in checks:
            if not valid:
                return detail
        expected_embedding_version = (
            manifest.embedding_model_version if expected_stage is CandidateSearchStage.DENSE_VECTOR else None
        )
        if hit.embedding_model_version != expected_embedding_version:
            return "embedding_model_version"
    return None


def _search_stages(
    manifest: CandidateIndexManifest,
    port: CandidateIndexSearchPort,
):
    stages = [
        (CandidateSearchStage.PRODUCT_NAME_EXACT, port.search_product_name_exact),
        (CandidateSearchStage.APPROVED_ALIAS_EXACT, port.search_approved_alias_exact),
        (CandidateSearchStage.TRIGRAM_EDIT_DISTANCE, port.search_trigram_edit_distance),
    ]
    if manifest.build_mode is CandidateIndexBuildMode.HYBRID:
        stages.append((CandidateSearchStage.DENSE_VECTOR, port.search_dense_vector))
    return tuple(stages)


def search_candidate_index(
    query: CandidateSearchQuery,
    manifest: CandidateIndexManifest,
    port: CandidateIndexSearchPort,
) -> CandidateIndexSearchSuccess | CandidateIndexSearchFailure:
    """Run Candidate retrieval stages while preserving internal stage-level evidence."""

    query_failure = _search_query_failure(query, manifest)
    if query_failure is not None:
        return query_failure
    collected: list[CandidateRawHit] = []
    for stage, search in _search_stages(manifest, port):
        try:
            hits = search(query, manifest)
        except Exception:
            return CandidateIndexSearchFailure(
                CandidateIndexSearchFailureReason.PORT_FAILURE,
                (stage.value,),
            )
        failure_detail = _hit_failure_detail(hits, stage, query, manifest)
        if failure_detail is not None:
            return CandidateIndexSearchFailure(
                CandidateIndexSearchFailureReason.HIT_PROVENANCE_MISMATCH,
                (failure_detail,),
            )
        collected.extend(hits)
    return CandidateIndexSearchSuccess(raw_hits=tuple(collected))
