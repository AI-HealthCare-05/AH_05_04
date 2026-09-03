from dataclasses import replace

import pytest

from ai_worker.tasks.rag.candidate_index import (
    CandidateAliasReviewStatus,
    CandidateCatalogCounts,
    CandidateCatalogExport,
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


def test_conflicting_member_content_fails_entire_build() -> None:
    catalog = valid_catalog()
    conflicting = replace(catalog.search_entries[0], normalized_text="충돌값")

    result = build_candidate_index(
        replace(
            catalog,
            search_entries=(*catalog.search_entries, conflicting),
            declared_counts=replace(catalog.declared_counts, search_entry_count=3),
        ),
        lexical_config(),
    )

    assert result == CandidateIndexBuildFailure(
        reason=CandidateIndexBuildFailureReason.MEMBER_CONFLICT,
        details=("search-entry-product-1",),
    )


def test_manifest_records_lexical_provenance_and_reproducible_hashes() -> None:
    result = build_candidate_index(valid_catalog(), lexical_config())

    assert isinstance(result, CandidateIndexBuildSuccess)
    assert result.manifest.index_kind is CandidateIndexKind.MEDICATION_CANDIDATE
    assert result.manifest.catalog_version == "catalog-v1"
    assert result.manifest.catalog_manifest_hash == "a" * 64
    assert result.manifest.source_snapshot_ids == ("snapshot-1",)
    assert result.manifest.source_versions == ("mfds-product-approval@v1",)
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
