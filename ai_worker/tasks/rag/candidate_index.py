"""Pure RAG-07A medication Candidate Index contracts and deterministic logic."""

from dataclasses import dataclass
from enum import StrEnum


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


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


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


def build_candidate_index(
    catalog: CandidateCatalogExport,
    config: CandidateIndexBuildConfig,
    embedding_port: object | None = None,
) -> CandidateIndexBuildFailure:
    """Validate the RAG-06 handoff before any Candidate members are constructed."""

    del embedding_port
    if catalog.verification_status is not CatalogVerificationStatus.APPROVED:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.CATALOG_NOT_APPROVED,
            ("verification_status",),
        )
    if catalog.freshness_status is not CatalogFreshnessStatus.CURRENT:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.CATALOG_STALE,
            ("freshness_status",),
        )
    if not catalog.is_complete:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.CATALOG_PARTIAL,
            ("is_complete",),
        )
    if catalog.duplicate_identity_count:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.DUPLICATE_PRODUCT_IDENTITY,
            ("duplicate_identity_count",),
        )
    if catalog.orphan_count:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.CATALOG_PARTIAL,
            ("orphan_count",),
        )
    if catalog.conflict_count:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.ALIAS_CONFLICT,
            ("conflict_count",),
        )
    if not _is_sha256(catalog.catalog_manifest_hash):
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID,
            ("catalog_manifest_hash",),
        )
    count_mismatches = _catalog_count_mismatches(catalog)
    if count_mismatches:
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.CATALOG_COUNT_MISMATCH,
            count_mismatches,
        )
    if not _config_is_valid(catalog, config):
        return CandidateIndexBuildFailure(
            CandidateIndexBuildFailureReason.BUILD_CONFIG_INVALID,
            ("config",),
        )
    raise NotImplementedError("candidate member build is implemented in the next TDD cycle")
