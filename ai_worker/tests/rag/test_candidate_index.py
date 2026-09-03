from dataclasses import replace

import pytest

from ai_worker.tasks.rag.candidate_index import (
    CandidateAliasReviewStatus,
    CandidateCatalogCounts,
    CandidateCatalogExport,
    CandidateDistanceMetric,
    CandidateEntityType,
    CandidateEntryType,
    CandidateIndexBuildConfig,
    CandidateIndexBuildFailure,
    CandidateIndexBuildFailureReason,
    CandidateIndexBuildMode,
    CandidateRecordStatus,
    CatalogAlias,
    CatalogComponent,
    CatalogFreshnessStatus,
    CatalogIngredient,
    CatalogProduct,
    CatalogSearchEntry,
    CatalogVerificationStatus,
    ProductIdentity,
    build_candidate_index,
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
        source_snapshot_ids=("snapshot-1",),
        source_versions=("mfds-product-approval@v1",),
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
    result = build_candidate_index(replace(valid_catalog(), **catalog_change), lexical_config())

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
    "config_change",
    [
        {"candidate_limit": 0},
        {"display_limit": 2},
        {"normalization_version": "other-normalization-v1"},
        {"embedding_provider": "synthetic-provider"},
        {"distance_metric": CandidateDistanceMetric.COSINE},
    ],
)
def test_invalid_lexical_config_fails_closed(config_change: dict[str, object]) -> None:
    result = build_candidate_index(valid_catalog(), replace(lexical_config(), **config_change))

    assert isinstance(result, CandidateIndexBuildFailure)
    assert result.reason is CandidateIndexBuildFailureReason.BUILD_CONFIG_INVALID
    assert not hasattr(result, "members")
    assert not hasattr(result, "manifest")
