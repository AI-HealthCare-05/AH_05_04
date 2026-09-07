"""RAG-06 Catalog와 RAG-07A Candidate Index 사이의 공유 타입 계약입니다."""

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


class CatalogComponentRole(StrEnum):
    ACTIVE_INGREDIENT = "ACTIVE_INGREDIENT"
    EXCIPIENT = "EXCIPIENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    entity_type: CandidateEntityType
    code_system: str
    canonical_code: str


@dataclass(frozen=True, slots=True)
class CatalogProduct:
    product_ref: str
    source_record_key: str
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
    source_record_key: str
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
    component_role: CatalogComponentRole = CatalogComponentRole.ACTIVE_INGREDIENT
    release_profile: str | None = None


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
    alias_source: str = "UNSPECIFIED"


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
class CandidateCatalogSourceRef:
    snapshot_id: str
    source_version: str


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
    source_refs: tuple[CandidateCatalogSourceRef, ...]
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
