from dataclasses import FrozenInstanceError

import pytest

from ai_worker.tasks.rag import candidate_index
from ai_worker.tasks.rag.catalog import (
    CandidateAliasReviewStatus,
    CandidateCatalogCounts,
    CandidateCatalogExport,
    CandidateCatalogSourceRef,
    CandidateEntityType,
    CandidateEntryType,
    CandidateRecordStatus,
    CatalogFreshnessStatus,
    CatalogProduct,
    CatalogSearchEntry,
    CatalogVerificationStatus,
    ProductIdentity,
)


def _identity() -> ProductIdentity:
    return ProductIdentity(
        entity_type=CandidateEntityType.PRODUCT,
        code_system="MFDS_ITEM_SEQ",
        canonical_code="synthetic-product-001",
    )


def test_candidate_index_reexports_catalog_contract_types() -> None:
    shared_types = (
        CandidateAliasReviewStatus,
        CandidateCatalogCounts,
        CandidateCatalogExport,
        CandidateCatalogSourceRef,
        CandidateEntityType,
        CandidateEntryType,
        CandidateRecordStatus,
        CatalogFreshnessStatus,
        CatalogProduct,
        CatalogSearchEntry,
        CatalogVerificationStatus,
        ProductIdentity,
    )

    for shared_type in shared_types:
        assert getattr(candidate_index, shared_type.__name__) is shared_type


def test_product_identity_is_immutable_and_keeps_official_identity_fields() -> None:
    identity = _identity()

    assert identity.entity_type is CandidateEntityType.PRODUCT
    assert identity.code_system == "MFDS_ITEM_SEQ"
    assert identity.canonical_code == "synthetic-product-001"
    with pytest.raises(FrozenInstanceError):
        identity.canonical_code = "changed"  # type: ignore[misc]


def test_catalog_source_reference_binds_snapshot_and_version() -> None:
    source_ref = CandidateCatalogSourceRef(
        snapshot_id="synthetic-snapshot-001",
        source_version="2026-09-07",
    )

    assert source_ref == CandidateCatalogSourceRef(
        snapshot_id="synthetic-snapshot-001",
        source_version="2026-09-07",
    )


def test_catalog_export_uses_tuple_collections_and_declared_counts() -> None:
    identity = _identity()
    product = CatalogProduct(
        product_ref="synthetic-product-row-001",
        identity=identity,
        product_name="합성 의약품",
        normalized_product_name="합성의약품",
        strength_text=None,
        dosage_form=None,
        manufacturer_name=None,
        source_snapshot_id="synthetic-snapshot-001",
        normalization_version="medication-catalog-normalization-v1",
        status=CandidateRecordStatus.ACTIVE,
    )
    search_entry = CatalogSearchEntry(
        entry_ref="synthetic-search-entry-001",
        product_ref=product.product_ref,
        identity=identity,
        entry_type=CandidateEntryType.PRODUCT_NAME,
        alias_ref=None,
        display_text=product.product_name,
        normalized_text=product.normalized_product_name,
        source_snapshot_id=product.source_snapshot_id,
        normalization_version=product.normalization_version,
        review_status=CandidateAliasReviewStatus.APPROVED,
        status=CandidateRecordStatus.ACTIVE,
    )
    catalog = CandidateCatalogExport(
        catalog_version="synthetic-catalog-v1",
        catalog_manifest_hash="a" * 64,
        source_refs=(
            CandidateCatalogSourceRef(
                snapshot_id="synthetic-snapshot-001",
                source_version="2026-09-07",
            ),
        ),
        schema_version="catalog-schema-v1",
        normalization_version="medication-catalog-normalization-v1",
        verification_status=CatalogVerificationStatus.APPROVED,
        freshness_status=CatalogFreshnessStatus.CURRENT,
        is_complete=True,
        products=(product,),
        ingredients=(),
        components=(),
        aliases=(),
        search_entries=(search_entry,),
        declared_counts=CandidateCatalogCounts(
            product_count=1,
            ingredient_count=0,
            component_count=0,
            alias_count=0,
            search_entry_count=1,
        ),
        duplicate_identity_count=0,
        orphan_count=0,
        conflict_count=0,
    )

    assert catalog.source_refs[0].snapshot_id == product.source_snapshot_id
    assert catalog.products == (product,)
    assert catalog.search_entries == (search_entry,)
