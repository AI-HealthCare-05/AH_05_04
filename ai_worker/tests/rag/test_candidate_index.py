import subprocess
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.rag.candidate_index import (
    CandidateAliasReviewStatus,
    CandidateCatalogCounts,
    CandidateCatalogExport,
    CandidateCatalogSourceRef,
    CandidateDistanceMetric,
    CandidateEmbeddingRequest,
    CandidateEmbeddingVector,
    CandidateEntityType,
    CandidateEntryType,
    CandidateIndexBuildConfig,
    CandidateIndexBuildFailure,
    CandidateIndexBuildFailureReason,
    CandidateIndexBuildMode,
    CandidateIndexBuildSuccess,
    CandidateIndexKind,
    CandidateIndexSearchFailure,
    CandidateIndexSearchFailureReason,
    CandidateIndexSearchSuccess,
    CandidateRawHit,
    CandidateRecordStatus,
    CandidateSearchQuery,
    CandidateSearchStage,
    CatalogAlias,
    CatalogComponent,
    CatalogFreshnessStatus,
    CatalogIngredient,
    CatalogProduct,
    CatalogSearchEntry,
    CatalogVerificationStatus,
    ProductIdentity,
    build_candidate_index,
    search_candidate_index,
)


def product_identity(code: str = "P-001") -> ProductIdentity:
    return ProductIdentity(
        entity_type=CandidateEntityType.PRODUCT,
        code_system="MFDS_ITEM_SEQ",
        canonical_code=code,
    )


def ingredient_identity() -> ProductIdentity:
    return ProductIdentity(
        entity_type=CandidateEntityType.INGREDIENT,
        code_system="MFDS_INGREDIENT_CODE",
        canonical_code="I-001",
    )


def valid_catalog() -> CandidateCatalogExport:
    product = CatalogProduct(
        product_ref="product-row-1",
        identity=product_identity(),
        product_name="가나다정",
        normalized_product_name="가나다정",
        strength_text="10mg",
        dosage_form="정제",
        manufacturer_name="합성제약",
        source_snapshot_id="snapshot-1",
        normalization_version="medication-catalog-normalization-v1",
        status=CandidateRecordStatus.ACTIVE,
    )
    ingredient = CatalogIngredient(
        ingredient_ref="ingredient-row-1",
        identity=ingredient_identity(),
        ingredient_name="합성성분",
        normalized_ingredient_name="합성성분",
        source_snapshot_id="snapshot-1",
        normalization_version="medication-catalog-normalization-v1",
        status=CandidateRecordStatus.ACTIVE,
    )
    component = CatalogComponent(
        component_ref="component-row-1",
        product_ref=product.product_ref,
        ingredient_ref=ingredient.ingredient_ref,
        component_order=1,
        strength_value="10",
        strength_unit="mg",
        source_snapshot_id="snapshot-1",
    )
    alias = CatalogAlias(
        alias_ref="alias-row-1",
        identity=product.identity,
        alias_text="가나다 정",
        normalized_alias="가나다정별칭",
        source_snapshot_id="snapshot-1",
        normalization_version="medication-catalog-normalization-v1",
        review_status=CandidateAliasReviewStatus.APPROVED,
        status=CandidateRecordStatus.ACTIVE,
        is_effective=True,
    )
    entries = (
        CatalogSearchEntry(
            entry_ref="search-entry-product-1",
            product_ref=product.product_ref,
            identity=product.identity,
            entry_type=CandidateEntryType.PRODUCT_NAME,
            alias_ref=None,
            display_text=product.product_name,
            normalized_text=product.normalized_product_name,
            source_snapshot_id="snapshot-1",
            normalization_version="medication-catalog-normalization-v1",
            review_status=CandidateAliasReviewStatus.APPROVED,
            status=CandidateRecordStatus.ACTIVE,
        ),
        CatalogSearchEntry(
            entry_ref="search-entry-alias-1",
            product_ref=product.product_ref,
            identity=product.identity,
            entry_type=CandidateEntryType.APPROVED_ALIAS,
            alias_ref=alias.alias_ref,
            display_text=alias.alias_text,
            normalized_text=alias.normalized_alias,
            source_snapshot_id="snapshot-1",
            normalization_version="medication-catalog-normalization-v1",
            review_status=CandidateAliasReviewStatus.APPROVED,
            status=CandidateRecordStatus.ACTIVE,
        ),
    )
    return CandidateCatalogExport(
        catalog_version="catalog-v1",
        catalog_manifest_hash="a" * 64,
        source_refs=(CandidateCatalogSourceRef(snapshot_id="snapshot-1", source_version="mfds-product-approval@v1"),),
        schema_version="candidate-catalog-export-v1",
        normalization_version="medication-catalog-normalization-v1",
        verification_status=CatalogVerificationStatus.APPROVED,
        freshness_status=CatalogFreshnessStatus.CURRENT,
        is_complete=True,
        products=(product,),
        ingredients=(ingredient,),
        components=(component,),
        aliases=(alias,),
        search_entries=entries,
        declared_counts=CandidateCatalogCounts(
            product_count=1,
            ingredient_count=1,
            component_count=1,
            alias_count=1,
            search_entry_count=2,
        ),
        duplicate_identity_count=0,
        orphan_count=0,
        conflict_count=0,
    )


def lexical_config() -> CandidateIndexBuildConfig:
    return CandidateIndexBuildConfig(
        index_code="MEDICATION_CANDIDATE",
        index_version="candidate-index-v1",
        normalization_version="medication-catalog-normalization-v1",
        lexical_config_version="candidate-lexical-v1",
        search_order_version="candidate-search-order-v1",
        candidate_limit=20,
        display_limit=1,
        build_mode=CandidateIndexBuildMode.LEXICAL_ONLY,
        embedding_provider=None,
        embedding_model=None,
        embedding_model_version=None,
        embedding_dimension=None,
        distance_metric=None,
        ann_config=None,
    )


def hybrid_config(dimension: int = 2) -> CandidateIndexBuildConfig:
    return replace(
        lexical_config(),
        build_mode=CandidateIndexBuildMode.HYBRID,
        embedding_provider="synthetic-provider",
        embedding_model="synthetic-model",
        embedding_model_version="synthetic-model-v1",
        embedding_dimension=dimension,
        distance_metric=CandidateDistanceMetric.COSINE,
        ann_config=(("hnsw_m", "16"),),
    )


def _nfd(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    assert decomposed != value
    return decomposed


def test_unapproved_catalog_fails_without_partial_output() -> None:
    result = build_candidate_index(
        replace(valid_catalog(), verification_status=CatalogVerificationStatus.NOT_APPROVED),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_NOT_APPROVED,
        details=("verification_status",),
    )
    assert not hasattr(result, "members")
    assert not hasattr(result, "manifest")


@pytest.mark.parametrize(
    ("catalog_change", "reason", "details"),
    [
        (
            {"freshness_status": CatalogFreshnessStatus.STALE},
            CandidateIndexBuildFailureReason.CATALOG_STALE,
            ("freshness_status",),
        ),
        (
            {"is_complete": False},
            CandidateIndexBuildFailureReason.CATALOG_PARTIAL,
            ("is_complete",),
        ),
        (
            {"catalog_manifest_hash": "not-sha256"},
            CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID,
            ("catalog_manifest_hash",),
        ),
        (
            {"orphan_count": 1},
            CandidateIndexBuildFailureReason.CATALOG_PARTIAL,
            ("orphan_count",),
        ),
    ],
)
def test_catalog_envelope_fails_closed(
    catalog_change: dict[str, object],
    reason: CandidateIndexBuildFailureReason,
    details: tuple[str, ...],
) -> None:
    result = build_candidate_index(
        replace(valid_catalog(), **cast(Any, catalog_change)),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(reason=reason, details=details)


def test_declared_catalog_count_mismatch_fails_closed() -> None:
    catalog = valid_catalog()
    result = build_candidate_index(
        replace(catalog, declared_counts=replace(catalog.declared_counts, search_entry_count=3)),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_COUNT_MISMATCH,
        details=("search_entry_count",),
    )


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (
            lambda catalog: replace(
                catalog,
                products=(replace(catalog.products[0], product_name=_nfd("가나다정")),),
            ),
            "products.product_name",
        ),
        (
            lambda catalog: replace(
                catalog,
                products=(replace(catalog.products[0], normalized_product_name=_nfd("가나다정")),),
            ),
            "products.normalized_product_name",
        ),
        (
            lambda catalog: replace(
                catalog,
                products=(replace(catalog.products[0], manufacturer_name=_nfd("합성제약")),),
            ),
            "products.manufacturer_name",
        ),
        (
            lambda catalog: replace(
                catalog,
                ingredients=(replace(catalog.ingredients[0], ingredient_name=_nfd("합성성분")),),
            ),
            "ingredients.ingredient_name",
        ),
        (
            lambda catalog: replace(
                catalog,
                components=(replace(catalog.components[0], strength_unit=_nfd("밀리그램")),),
            ),
            "components.strength_unit",
        ),
        (
            lambda catalog: replace(
                catalog,
                aliases=(replace(catalog.aliases[0], alias_text=_nfd("가나다 정")),),
            ),
            "aliases.alias_text",
        ),
        (
            lambda catalog: replace(
                catalog,
                aliases=(replace(catalog.aliases[0], normalized_alias=_nfd("가나다정별칭")),),
            ),
            "aliases.normalized_alias",
        ),
        (
            lambda catalog: replace(
                catalog,
                search_entries=(
                    replace(catalog.search_entries[0], display_text=_nfd("가나다정")),
                    catalog.search_entries[1],
                ),
            ),
            "search_entries.display_text",
        ),
        (
            lambda catalog: replace(
                catalog,
                search_entries=(
                    replace(catalog.search_entries[0], normalized_text=_nfd("가나다정")),
                    catalog.search_entries[1],
                ),
            ),
            "search_entries.normalized_text",
        ),
    ],
)
def test_catalog_text_must_be_nfc_before_build(
    mutate: Callable[[CandidateCatalogExport], CandidateCatalogExport],
    detail: str,
) -> None:
    result = build_candidate_index(mutate(valid_catalog()), lexical_config())

    assert isinstance(result, CandidateIndexBuildFailure)
    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_TEXT_NOT_NFC,
        details=(detail,),
    )
    assert not hasattr(result, "members")
    assert not hasattr(result, "manifest")


def test_build_config_text_must_be_nfc() -> None:
    result = build_candidate_index(
        valid_catalog(),
        replace(lexical_config(), index_code=_nfd("의약품후보")),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.BUILD_CONFIG_INVALID,
        details=("config",),
    )


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda catalog: replace(catalog, catalog_version=""), "catalog_version"),
        (lambda catalog: replace(catalog, schema_version=""), "schema_version"),
        (lambda catalog: replace(catalog, catalog_version="   "), "catalog_version"),
        (
            lambda catalog: replace(
                catalog,
                products=(replace(catalog.products[0], product_name=""),),
            ),
            "products.product_name",
        ),
        (
            lambda catalog: replace(
                catalog,
                search_entries=(
                    replace(catalog.search_entries[0], entry_ref=""),
                    catalog.search_entries[1],
                ),
            ),
            "search_entries.entry_ref",
        ),
    ],
)
def test_blank_required_catalog_field_fails_closed(
    mutate: Callable[[CandidateCatalogExport], CandidateCatalogExport],
    detail: str,
) -> None:
    result = build_candidate_index(mutate(valid_catalog()), lexical_config())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_REQUIRED_FIELD_INVALID,
        details=(detail,),
    )
    assert not hasattr(result, "members")
    assert not hasattr(result, "manifest")


def test_invalid_catalog_manifest_hash_type_fails_closed_without_crash() -> None:
    result = build_candidate_index(
        replace(valid_catalog(), catalog_manifest_hash=cast(Any, None)),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID,
        details=("catalog_manifest_hash",),
    )


def test_invalid_product_identity_enum_type_fails_closed_without_crash() -> None:
    catalog = valid_catalog()
    invalid_identity = replace(catalog.products[0].identity, entity_type=cast(Any, "PRODUCT"))

    result = build_candidate_index(
        replace(catalog, products=(replace(catalog.products[0], identity=invalid_identity),)),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
        details=("products.identity",),
    )


def test_invalid_source_version_type_fails_closed_without_crash() -> None:
    catalog = valid_catalog()
    invalid_source_ref = replace(catalog.source_refs[0], source_version=cast(Any, 1))

    result = build_candidate_index(
        replace(catalog, source_refs=(invalid_source_ref,)),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_SOURCE_BINDING_INVALID,
        details=("source_refs",),
    )


@pytest.mark.parametrize(
    ("catalog_change", "detail"),
    [
        ({"source_refs": ()}, "source_refs"),
        (
            {"products": (), "declared_counts": replace(valid_catalog().declared_counts, product_count=0)},
            "products",
        ),
    ],
)
def test_empty_required_catalog_collection_fails_closed(
    catalog_change: dict[str, object],
    detail: str,
) -> None:
    result = build_candidate_index(
        replace(valid_catalog(), **cast(Any, catalog_change)),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_REQUIRED_FIELD_INVALID,
        details=(detail,),
    )


def test_zero_members_after_filtering_fails_closed() -> None:
    catalog = valid_catalog()
    inactive_entries = tuple(replace(entry, status=CandidateRecordStatus.INACTIVE) for entry in catalog.search_entries)

    result = build_candidate_index(replace(catalog, search_entries=inactive_entries), lexical_config())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_REQUIRED_FIELD_INVALID,
        details=("members",),
    )


@pytest.mark.parametrize(
    "source_refs",
    [
        (
            CandidateCatalogSourceRef(snapshot_id="snapshot-1", source_version="mfds-product-approval@v1"),
            CandidateCatalogSourceRef(snapshot_id="snapshot-1", source_version="mfds-product-approval@v1"),
        ),
        (
            CandidateCatalogSourceRef(snapshot_id="snapshot-1", source_version="mfds-product-approval@v1"),
            CandidateCatalogSourceRef(snapshot_id="snapshot-1", source_version="mfds-product-approval@v2"),
        ),
    ],
)
def test_source_ref_binding_conflict_fails_closed(source_refs: tuple[CandidateCatalogSourceRef, ...]) -> None:
    result = build_candidate_index(replace(valid_catalog(), source_refs=source_refs), lexical_config())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.CATALOG_SOURCE_BINDING_INVALID,
        details=("snapshot-1",),
    )


@pytest.mark.parametrize(
    "config_change",
    [
        {"candidate_limit": 0},
        {"candidate_limit": True},
        {"candidate_limit": "20"},
        {"display_limit": 2},
        {"display_limit": True},
        {"index_code": 123},
        {"normalization_version": "other-normalization-v1"},
        {"embedding_provider": "synthetic-provider"},
        {"distance_metric": CandidateDistanceMetric.COSINE},
    ],
)
def test_invalid_lexical_config_fails_closed(config_change: dict[str, object]) -> None:
    result = build_candidate_index(
        valid_catalog(),
        replace(lexical_config(), **cast(Any, config_change)),
    )

    assert isinstance(result, CandidateIndexBuildFailure)
    assert result.reason is CandidateIndexBuildFailureReason.BUILD_CONFIG_INVALID
    assert not hasattr(result, "members")
    assert not hasattr(result, "manifest")


@pytest.mark.parametrize(
    "config_change",
    [
        {"build_mode": "HYBRID"},
        {"ann_config": ()},
        {"ann_config": (("hnsw_m", "16"), ("hnsw_m", "32"))},
        {"ann_config": (("", "16"),)},
        {"ann_config": (("hnsw_m", "   "),)},
        {"embedding_provider": 123},
        {"embedding_dimension": True},
        {"embedding_dimension": "2"},
    ],
)
def test_invalid_hybrid_config_fails_closed(config_change: dict[str, object]) -> None:
    result = build_candidate_index(
        valid_catalog(),
        replace(hybrid_config(), **cast(Any, config_change)),
        embedding_port=FixedEmbeddingPort(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.BUILD_CONFIG_INVALID,
        details=("config",),
    )


def test_lexical_build_is_deterministic_across_input_order() -> None:
    catalog = valid_catalog()
    reversed_catalog = replace(
        catalog,
        products=tuple(reversed(catalog.products)),
        ingredients=tuple(reversed(catalog.ingredients)),
        components=tuple(reversed(catalog.components)),
        aliases=tuple(reversed(catalog.aliases)),
        search_entries=tuple(reversed(catalog.search_entries)),
    )

    forward = build_candidate_index(catalog, lexical_config())
    backward = build_candidate_index(reversed_catalog, lexical_config())

    assert isinstance(forward, CandidateIndexBuildSuccess)
    assert isinstance(backward, CandidateIndexBuildSuccess)
    assert forward == backward
    assert tuple(member.entry_type for member in forward.members) == (
        CandidateEntryType.APPROVED_ALIAS,
        CandidateEntryType.PRODUCT_NAME,
    )


def test_identical_repeated_search_entry_is_collapsed() -> None:
    catalog = valid_catalog()
    repeated = (*catalog.search_entries, catalog.search_entries[0])

    result = build_candidate_index(
        replace(
            catalog,
            search_entries=repeated,
            declared_counts=replace(catalog.declared_counts, search_entry_count=3),
        ),
        lexical_config(),
    )

    assert isinstance(result, CandidateIndexBuildSuccess)
    assert len(result.members) == 2


def test_conflicting_reuse_of_search_entry_ref_fails_closed() -> None:
    catalog = valid_catalog()
    second_product = replace(
        catalog.products[0],
        product_ref="product-row-2",
        identity=product_identity("P-002"),
    )
    conflicting_entry = replace(
        catalog.search_entries[0],
        product_ref=second_product.product_ref,
        identity=second_product.identity,
    )

    result = build_candidate_index(
        replace(
            catalog,
            products=(*catalog.products, second_product),
            search_entries=(*catalog.search_entries, conflicting_entry),
            declared_counts=replace(catalog.declared_counts, product_count=2, search_entry_count=3),
        ),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.MEMBER_CONFLICT,
        details=("search-entry-product-1",),
    )


def test_same_name_different_official_product_identities_remain_distinct() -> None:
    catalog = valid_catalog()
    first_product = catalog.products[0]
    second_product = replace(
        first_product,
        product_ref="product-row-2",
        identity=product_identity("P-002"),
    )
    second_entry = replace(
        catalog.search_entries[0],
        entry_ref="search-entry-product-2",
        product_ref=second_product.product_ref,
        identity=second_product.identity,
    )

    result = build_candidate_index(
        replace(
            catalog,
            products=(*catalog.products, second_product),
            search_entries=(*catalog.search_entries, second_entry),
            declared_counts=replace(catalog.declared_counts, product_count=2, search_entry_count=3),
        ),
        lexical_config(),
    )

    assert isinstance(result, CandidateIndexBuildSuccess)
    assert {member.identity.canonical_code for member in result.members} == {"P-001", "P-002"}
    assert result.manifest.product_identity_count == 2


def test_duplicate_official_product_identity_definition_fails_closed() -> None:
    catalog = valid_catalog()
    duplicate = replace(catalog.products[0], product_ref="product-row-duplicate")

    result = build_candidate_index(
        replace(
            catalog,
            products=(*catalog.products, duplicate),
            declared_counts=replace(catalog.declared_counts, product_count=2),
        ),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.DUPLICATE_PRODUCT_IDENTITY,
        details=("PRODUCT:MFDS_ITEM_SEQ:P-001",),
    )


def test_orphan_alias_search_entry_fails_closed() -> None:
    catalog = valid_catalog()
    orphan_entry = replace(catalog.search_entries[1], alias_ref="missing-alias")

    result = build_candidate_index(
        replace(catalog, search_entries=(catalog.search_entries[0], orphan_entry)),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
        details=("search-entry-alias-1",),
    )


def test_ingredient_alias_does_not_create_product_candidate_member() -> None:
    catalog = valid_catalog()
    ingredient_alias = replace(
        catalog.aliases[0],
        alias_ref="ingredient-alias-1",
        identity=catalog.ingredients[0].identity,
        alias_text="합성 성분",
        normalized_alias="합성성분별칭",
    )

    result = build_candidate_index(
        replace(
            catalog,
            aliases=(*catalog.aliases, ingredient_alias),
            declared_counts=replace(catalog.declared_counts, alias_count=2),
        ),
        lexical_config(),
    )

    assert isinstance(result, CandidateIndexBuildSuccess)
    assert {member.entry_ref for member in result.members} == {
        "search-entry-product-1",
        "search-entry-alias-1",
    }


@pytest.mark.parametrize(
    "entry_change",
    [
        {"display_text": "전혀다른이름"},
        {"normalized_text": "전혀다른정규명"},
    ],
)
def test_product_name_search_entry_must_match_product_row(entry_change: dict[str, str]) -> None:
    catalog = valid_catalog()
    conflicting = replace(catalog.search_entries[0], **cast(Any, entry_change))

    result = build_candidate_index(
        replace(
            catalog,
            search_entries=(conflicting, catalog.search_entries[1]),
        ),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
        details=("search-entry-product-1",),
    )


def test_active_approved_product_name_entry_referencing_inactive_product_fails_closed() -> None:
    catalog = valid_catalog()
    inactive_product = replace(catalog.products[0], status=CandidateRecordStatus.INACTIVE)

    result = build_candidate_index(replace(catalog, products=(inactive_product,)), lexical_config())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
        details=("search-entry-product-1",),
    )


def test_active_approved_alias_entry_referencing_inactive_product_fails_closed() -> None:
    catalog = valid_catalog()
    inactive_product = replace(catalog.products[0], status=CandidateRecordStatus.INACTIVE)
    alias_only_entries = (catalog.search_entries[1],)

    result = build_candidate_index(
        replace(
            catalog,
            products=(inactive_product,),
            search_entries=alias_only_entries,
            declared_counts=replace(catalog.declared_counts, search_entry_count=1),
        ),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.REFERENTIAL_INTEGRITY_INVALID,
        details=("search-entry-alias-1",),
    )


def test_alias_source_snapshot_is_preserved_separately_from_product_and_entry() -> None:
    catalog = valid_catalog()
    second_ref = CandidateCatalogSourceRef(snapshot_id="snapshot-2", source_version="mfds-alias-approval@v1")
    alias_with_own_snapshot = replace(catalog.aliases[0], source_snapshot_id="snapshot-2")
    alias_entry_with_own_snapshot = replace(catalog.search_entries[1], source_snapshot_id="snapshot-2")

    result = build_candidate_index(
        replace(
            catalog,
            source_refs=(*catalog.source_refs, second_ref),
            aliases=(alias_with_own_snapshot,),
            search_entries=(catalog.search_entries[0], alias_entry_with_own_snapshot),
        ),
        lexical_config(),
    )

    assert isinstance(result, CandidateIndexBuildSuccess)
    members_by_ref = {member.entry_ref: member for member in result.members}
    product_name_member = members_by_ref["search-entry-product-1"]
    alias_member = members_by_ref["search-entry-alias-1"]

    assert product_name_member.product_source_snapshot_id == "snapshot-1"
    assert product_name_member.entry_source_snapshot_id == "snapshot-1"
    assert product_name_member.alias_source_snapshot_id is None

    assert alias_member.product_source_snapshot_id == "snapshot-1"
    assert alias_member.entry_source_snapshot_id == "snapshot-2"
    assert alias_member.alias_source_snapshot_id == "snapshot-2"


def test_alias_target_conflict_detail_excludes_alias_text() -> None:
    catalog = valid_catalog()
    second_product = replace(
        catalog.products[0],
        product_ref="product-row-2",
        identity=product_identity("P-002"),
    )
    conflicting_alias = replace(
        catalog.aliases[0],
        alias_ref="alias-row-2",
        identity=second_product.identity,
    )

    result = build_candidate_index(
        replace(
            catalog,
            products=(*catalog.products, second_product),
            aliases=(*catalog.aliases, conflicting_alias),
            declared_counts=replace(catalog.declared_counts, product_count=2, alias_count=2),
        ),
        lexical_config(),
    )

    assert isinstance(result, CandidateIndexBuildFailure)
    assert result.reason is CandidateIndexBuildFailureReason.ALIAS_CONFLICT
    assert result.details == ("alias-row-1", "alias-row-2")
    assert catalog.aliases[0].normalized_alias not in result.details
    assert catalog.aliases[0].alias_text not in result.details


def test_manifest_records_lexical_provenance_and_reproducible_hashes() -> None:
    result = build_candidate_index(valid_catalog(), lexical_config())

    assert isinstance(result, CandidateIndexBuildSuccess)
    assert result.manifest.index_kind is CandidateIndexKind.MEDICATION_CANDIDATE
    assert result.manifest.catalog_version == "catalog-v1"
    assert result.manifest.catalog_manifest_hash == "a" * 64
    assert result.manifest.source_refs == (
        CandidateCatalogSourceRef(snapshot_id="snapshot-1", source_version="mfds-product-approval@v1"),
    )
    assert result.manifest.member_count == 2
    assert result.manifest.product_identity_count == 1
    assert result.manifest.product_name_count == 1
    assert result.manifest.approved_alias_count == 1
    assert result.manifest.vector_count == 0
    for value in (
        result.manifest.member_set_hash,
        result.manifest.configuration_hash,
        result.manifest.content_hash,
    ):
        assert len(value) == 64
        assert set(value) <= set("0123456789abcdef")

    repeated = build_candidate_index(valid_catalog(), lexical_config())
    changed = build_candidate_index(
        replace(valid_catalog(), catalog_version="catalog-v2"),
        lexical_config(),
    )
    assert isinstance(repeated, CandidateIndexBuildSuccess)
    assert isinstance(changed, CandidateIndexBuildSuccess)
    assert repeated.manifest.content_hash == result.manifest.content_hash
    assert changed.manifest.content_hash != result.manifest.content_hash


class FixedEmbeddingPort:
    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]:
        assert config.embedding_model_version == "synthetic-model-v1"
        assert tuple(request.normalized_text for request in requests) == ("가나다정별칭", "가나다정")
        return tuple(
            CandidateEmbeddingVector(member_key=request.member_key, values=vector)
            for request, vector in zip(requests, ((1.0, 0.0), (0.0, 1.0)), strict=True)
        )


def test_hybrid_build_binds_vectors_to_sorted_members() -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config(), FixedEmbeddingPort())

    assert isinstance(result, CandidateIndexBuildSuccess)
    assert tuple(member.embedding for member in result.members) == ((1.0, 0.0), (0.0, 1.0))
    assert result.manifest.vector_count == 2
    assert result.manifest.embedding_provider == "synthetic-provider"
    assert result.manifest.embedding_model == "synthetic-model"
    assert result.manifest.embedding_model_version == "synthetic-model-v1"
    assert result.manifest.embedding_dimension == 2
    assert result.manifest.distance_metric is CandidateDistanceMetric.COSINE


def test_ann_configuration_order_does_not_change_manifest_hashes() -> None:
    first_config = replace(
        hybrid_config(),
        ann_config=(("hnsw_m", "16"), ("ef_construction", "64")),
    )
    reversed_config = replace(first_config, ann_config=tuple(reversed(first_config.ann_config or ())))

    first = build_candidate_index(valid_catalog(), first_config, FixedEmbeddingPort())
    reversed_result = build_candidate_index(valid_catalog(), reversed_config, FixedEmbeddingPort())

    assert isinstance(first, CandidateIndexBuildSuccess)
    assert isinstance(reversed_result, CandidateIndexBuildSuccess)
    assert first == reversed_result


class StaticEmbeddingPort:
    def __init__(self, vectors: tuple[tuple[float, ...], ...], *, reverse_keys: bool = False) -> None:
        self.vectors = vectors
        self.reverse_keys = reverse_keys

    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]:
        del config
        keys = tuple(request.member_key for request in requests)
        if self.reverse_keys:
            keys = tuple(reversed(keys))
        return tuple(
            CandidateEmbeddingVector(member_key=member_key, values=vector)
            for member_key, vector in zip(keys, self.vectors, strict=False)
        )


@pytest.mark.parametrize(
    "port",
    [
        StaticEmbeddingPort(((1.0, 0.0),)),
        StaticEmbeddingPort(((1.0,), (0.0, 1.0))),
        StaticEmbeddingPort(((float("nan"), 0.0), (0.0, 1.0))),
        StaticEmbeddingPort(((float("inf"), 0.0), (0.0, 1.0))),
        StaticEmbeddingPort(((1.0, 0.0), (0.0, 1.0)), reverse_keys=True),
    ],
)
def test_invalid_embedding_output_fails_without_partial_output(port: StaticEmbeddingPort) -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config(), port)

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
        details=("embedding_output",),
    )
    assert not hasattr(result, "members")
    assert not hasattr(result, "manifest")


def test_missing_embedding_port_fails_without_partial_output() -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
        details=("embedding_port",),
    )


class FailingEmbeddingPort:
    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]:
        del requests, config
        raise RuntimeError("provider raw payload must not escape")


def test_embedding_provider_error_is_replaced_with_safe_failure() -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config(), FailingEmbeddingPort())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
        details=("embedding_port",),
    )


class NullEmbeddingPort:
    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]:
        del requests, config
        return cast(Any, None)


def test_embedding_port_returning_invalid_container_fails_closed_without_crash() -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config(), NullEmbeddingPort())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
        details=("embedding_output",),
    )


class MutableValuesEmbeddingPort:
    def embed(
        self,
        requests: tuple[CandidateEmbeddingRequest, ...],
        config: CandidateIndexBuildConfig,
    ) -> tuple[CandidateEmbeddingVector, ...]:
        del config
        return tuple(
            CandidateEmbeddingVector(
                member_key=request.member_key,
                values=cast(Any, [1.0, 0.0]),
            )
            for request in requests
        )


def test_embedding_port_returning_mutable_vector_fails_closed() -> None:
    result = build_candidate_index(valid_catalog(), hybrid_config(), MutableValuesEmbeddingPort())

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.EMBEDDING_OUTPUT_INVALID,
        details=("embedding_output",),
    )


def lexical_manifest():
    result = build_candidate_index(valid_catalog(), lexical_config())
    assert isinstance(result, CandidateIndexBuildSuccess)
    return result.manifest


def hybrid_manifest():
    result = build_candidate_index(valid_catalog(), hybrid_config(), FixedEmbeddingPort())
    assert isinstance(result, CandidateIndexBuildSuccess)
    return result.manifest


def valid_query(limit: int = 5) -> CandidateSearchQuery:
    return CandidateSearchQuery(
        index_version="candidate-index-v1",
        normalized_query="가나다정",
        retrieval_limit=limit,
    )


class RecordingSearchPort:
    def __init__(self) -> None:
        self.calls: list[CandidateSearchStage] = []

    def _hit(self, stage: CandidateSearchStage, manifest) -> tuple[CandidateRawHit, ...]:
        self.calls.append(stage)
        return (
            CandidateRawHit(
                identity=product_identity(),
                member_key="b" * 64,
                stage=stage,
                rank=1,
                stage_score=1.0,
                index_version=manifest.index_version,
                catalog_version=manifest.catalog_version,
                source_snapshot_id="snapshot-1",
                normalization_version=manifest.normalization_version,
                embedding_model_version=(
                    manifest.embedding_model_version if stage is CandidateSearchStage.DENSE_VECTOR else None
                ),
            ),
        )

    def search_product_name_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        assert query.retrieval_limit == 5
        return self._hit(CandidateSearchStage.PRODUCT_NAME_EXACT, manifest)

    def search_approved_alias_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        assert query.retrieval_limit == 5
        return self._hit(CandidateSearchStage.APPROVED_ALIAS_EXACT, manifest)

    def search_trigram_edit_distance(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        assert query.retrieval_limit == 5
        return self._hit(CandidateSearchStage.TRIGRAM_EDIT_DISTANCE, manifest)

    def search_dense_vector(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        assert query.retrieval_limit == 5
        return self._hit(CandidateSearchStage.DENSE_VECTOR, manifest)


def test_lexical_search_calls_stages_in_order_and_preserves_repeated_identity_hits() -> None:
    port = RecordingSearchPort()

    result = search_candidate_index(valid_query(), lexical_manifest(), port)

    assert isinstance(result, CandidateIndexSearchSuccess)
    assert port.calls == [
        CandidateSearchStage.PRODUCT_NAME_EXACT,
        CandidateSearchStage.APPROVED_ALIAS_EXACT,
        CandidateSearchStage.TRIGRAM_EDIT_DISTANCE,
    ]
    assert tuple(hit.stage for hit in result.raw_hits) == tuple(port.calls)
    assert [hit.identity.canonical_code for hit in result.raw_hits] == ["P-001"] * 3


def test_hybrid_search_calls_dense_only_as_final_auxiliary_stage() -> None:
    port = RecordingSearchPort()

    result = search_candidate_index(valid_query(), hybrid_manifest(), port)

    assert isinstance(result, CandidateIndexSearchSuccess)
    assert port.calls == [
        CandidateSearchStage.PRODUCT_NAME_EXACT,
        CandidateSearchStage.APPROVED_ALIAS_EXACT,
        CandidateSearchStage.TRIGRAM_EDIT_DISTANCE,
        CandidateSearchStage.DENSE_VECTOR,
    ]


@pytest.mark.parametrize(
    "query",
    [
        CandidateSearchQuery("candidate-index-v1", "", 5),
        CandidateSearchQuery("candidate-index-v1", "   ", 5),
        CandidateSearchQuery("candidate-index-v1", "가나다정", 0),
        CandidateSearchQuery("candidate-index-v1", "가나다정", 21),
        CandidateSearchQuery("candidate-index-v1", "가나다정", cast(Any, "5")),
        CandidateSearchQuery("candidate-index-v1", "가나다정", cast(Any, True)),
    ],
)
def test_invalid_search_query_fails_before_port_call(query: CandidateSearchQuery) -> None:
    port = RecordingSearchPort()

    result = search_candidate_index(query, lexical_manifest(), port)

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.QUERY_INVALID,
        details=("query",),
        raw_hits=(),
    )
    assert port.calls == []


class MismatchedHitPort(RecordingSearchPort):
    def __init__(self, field: str) -> None:
        super().__init__()
        self.field = field

    def _hit(self, stage: CandidateSearchStage, manifest) -> tuple[CandidateRawHit, ...]:
        hit = super()._hit(stage, manifest)[0]
        return (replace(hit, **cast(Any, {self.field: "mismatch"})),)


@pytest.mark.parametrize(
    "field",
    ["index_version", "catalog_version", "source_snapshot_id", "normalization_version"],
)
def test_hit_provenance_mismatch_fails_closed(field: str) -> None:
    result = search_candidate_index(valid_query(), lexical_manifest(), MismatchedHitPort(field))

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.HIT_PROVENANCE_MISMATCH,
        details=(field,),
        raw_hits=(),
    )


class WrongStagePort(RecordingSearchPort):
    def search_product_name_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        return self._hit(CandidateSearchStage.APPROVED_ALIAS_EXACT, manifest)


def test_stage_mismatch_fails_without_returning_partial_hits() -> None:
    result = search_candidate_index(valid_query(), lexical_manifest(), WrongStagePort())

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.HIT_PROVENANCE_MISMATCH,
        details=("stage",),
        raw_hits=(),
    )


class MalformedFirstHitPort(RecordingSearchPort):
    def __init__(self, changes: dict[str, object]) -> None:
        super().__init__()
        self.changes = changes

    def search_product_name_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        hit = self._hit(CandidateSearchStage.PRODUCT_NAME_EXACT, manifest)[0]
        return (replace(hit, **cast(Any, self.changes)),)


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"rank": 2}, "rank"),
        ({"stage_score": float("nan")}, "stage_score"),
        ({"member_key": "not-sha256"}, "member_key"),
        ({"embedding_model_version": "unexpected-model"}, "embedding_model_version"),
    ],
)
def test_malformed_hit_fails_without_returning_partial_hits(changes: dict[str, object], detail: str) -> None:
    result = search_candidate_index(valid_query(), lexical_manifest(), MalformedFirstHitPort(changes))

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.HIT_PROVENANCE_MISMATCH,
        details=(detail,),
        raw_hits=(),
    )


class TooManyHitsPort(RecordingSearchPort):
    def search_product_name_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        hit = self._hit(CandidateSearchStage.PRODUCT_NAME_EXACT, manifest)[0]
        return tuple(replace(hit, rank=rank) for rank in range(1, query.retrieval_limit + 2))


def test_stage_result_over_retrieval_limit_fails_closed() -> None:
    result = search_candidate_index(valid_query(), lexical_manifest(), TooManyHitsPort())

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.HIT_PROVENANCE_MISMATCH,
        details=("retrieval_limit",),
        raw_hits=(),
    )


class FailingSearchPort(RecordingSearchPort):
    def search_product_name_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        del query, manifest
        raise RuntimeError("database raw error must not escape")


def test_search_port_error_is_replaced_with_safe_failure() -> None:
    result = search_candidate_index(valid_query(), lexical_manifest(), FailingSearchPort())

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.PORT_FAILURE,
        details=(CandidateSearchStage.PRODUCT_NAME_EXACT.value,),
        raw_hits=(),
    )


class NullSearchPort(RecordingSearchPort):
    def search_product_name_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        del query, manifest
        return cast(Any, None)


def test_search_port_returning_invalid_container_fails_closed_without_crash() -> None:
    result = search_candidate_index(valid_query(), lexical_manifest(), NullSearchPort())

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.PORT_FAILURE,
        details=(CandidateSearchStage.PRODUCT_NAME_EXACT.value,),
        raw_hits=(),
    )


class InvalidNestedHitPort(RecordingSearchPort):
    def __init__(self, identity: object) -> None:
        super().__init__()
        self.identity = identity

    def search_product_name_exact(self, query, manifest) -> tuple[CandidateRawHit, ...]:
        hit = self._hit(CandidateSearchStage.PRODUCT_NAME_EXACT, manifest)[0]
        return (replace(hit, identity=cast(Any, self.identity)),)


@pytest.mark.parametrize(
    "identity",
    [
        None,
        ProductIdentity(CandidateEntityType.PRODUCT, "", "P-001"),
        ProductIdentity(CandidateEntityType.PRODUCT, "MFDS_ITEM_SEQ", ""),
    ],
)
def test_search_port_returning_invalid_nested_hit_fails_closed(identity: object) -> None:
    result = search_candidate_index(valid_query(), lexical_manifest(), InvalidNestedHitPort(identity))

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.PORT_FAILURE,
        details=(CandidateSearchStage.PRODUCT_NAME_EXACT.value,),
        raw_hits=(),
    )


def test_query_index_version_must_match_manifest() -> None:
    result = search_candidate_index(
        replace(valid_query(), index_version="candidate-index-v2"),
        lexical_manifest(),
        RecordingSearchPort(),
    )

    assert result == CandidateIndexSearchFailure(
        reason=CandidateIndexSearchFailureReason.INDEX_VERSION_MISMATCH,
        details=("index_version",),
        raw_hits=(),
    )


def test_candidate_index_import_does_not_load_backend_or_database_modules() -> None:
    project_root = Path(__file__).parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import ai_worker.tasks.rag.candidate_index; "
                "assert 'sqlalchemy' not in sys.modules; "
                "assert not any(name.startswith('backend.app') for name in sys.modules)"
            ),
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
