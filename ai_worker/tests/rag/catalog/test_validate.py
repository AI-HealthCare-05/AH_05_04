from dataclasses import replace

from ai_worker.tasks.rag.catalog import (
    CandidateAliasReviewStatus,
    CandidateEntityType,
    CandidateRecordStatus,
    CatalogAliasInput,
    CatalogComponentInput,
    CatalogComponentRole,
    CatalogIngredientInput,
    CatalogProductInput,
    CatalogValidationFailureReason,
    build_catalog_members,
    validate_catalog_members,
)


def _product(
    code: str,
    *,
    snapshot_id: str = "snapshot-001",
    status: CandidateRecordStatus = CandidateRecordStatus.ACTIVE,
) -> CatalogProductInput:
    return CatalogProductInput(
        source_snapshot_id=snapshot_id,
        source_record_key=f"record-{snapshot_id}-{code}",
        code_system="MFDS_ITEM_SEQ",
        canonical_code=code,
        product_name=f"합성 제품 {code}",
        product_status=status,
    )


def _alias(
    code: str,
    *,
    alias_ref: str,
    review_status: CandidateAliasReviewStatus = CandidateAliasReviewStatus.APPROVED,
    status: CandidateRecordStatus = CandidateRecordStatus.ACTIVE,
    is_effective: bool = True,
) -> CatalogAliasInput:
    return CatalogAliasInput(
        source_snapshot_id="snapshot-001",
        target_source_snapshot_id="snapshot-001",
        source_alias_ref=alias_ref,
        target_type=CandidateEntityType.PRODUCT,
        target_code_system="MFDS_ITEM_SEQ",
        target_canonical_code=code,
        alias_source="SYNTHETIC_REVIEWED",
        alias_text="동일 합성 별칭",
        review_status=review_status,
        status=status,
        is_effective=is_effective,
    )


def test_conflicting_active_product_aliases_fail_with_fixed_reason() -> None:
    members = build_catalog_members(
        products=(_product("P-001"), _product("P-002")),
        components=(),
        aliases=(
            _alias("P-001", alias_ref="alias-001"),
            _alias("P-002", alias_ref="alias-002"),
        ),
    )

    report = validate_catalog_members(members)

    assert report.is_valid is False
    assert report.conflict_count == 1
    assert report.failures[0].reason is CatalogValidationFailureReason.ALIAS_CONFLICT
    assert "동일 합성 별칭" not in str(report.failures)


def test_unapproved_inactive_or_ineffective_aliases_do_not_conflict() -> None:
    excluded_aliases = (
        _alias(
            "P-002",
            alias_ref="alias-pending",
            review_status=CandidateAliasReviewStatus.PENDING,
        ),
        _alias(
            "P-002",
            alias_ref="alias-inactive",
            status=CandidateRecordStatus.INACTIVE,
        ),
        _alias(
            "P-002",
            alias_ref="alias-ineffective",
            is_effective=False,
        ),
    )

    for excluded_alias in excluded_aliases:
        members = build_catalog_members(
            products=(_product("P-001"), _product("P-002")),
            components=(),
            aliases=(
                _alias("P-001", alias_ref="alias-approved"),
                excluded_alias,
            ),
        )

        assert validate_catalog_members(members).is_valid is True


def test_duplicate_official_identity_across_snapshots_is_rejected() -> None:
    members = build_catalog_members(
        products=(
            _product("P-001", snapshot_id="snapshot-001"),
            _product("P-001", snapshot_id="snapshot-002"),
        ),
        components=(),
        aliases=(),
    )

    report = validate_catalog_members(members)

    assert report.duplicate_identity_count == 1
    assert report.failures[0].reason is CatalogValidationFailureReason.DUPLICATE_PRODUCT_IDENTITY


def test_orphan_component_is_rejected_without_source_value_exposure() -> None:
    component = CatalogComponentInput(
        source_snapshot_id="snapshot-001",
        product_code_system="MFDS_ITEM_SEQ",
        product_canonical_code="P-001",
        ingredient_code_system="MFDS_INGREDIENT_CODE",
        ingredient_canonical_code="I-001",
        component_role=CatalogComponentRole.ACTIVE_INGREDIENT,
        component_order=1,
        strength_value="10",
        strength_unit="mg",
    )
    members = build_catalog_members(
        products=(_product("P-001"),),
        ingredients=(
            CatalogIngredientInput(
                "snapshot-001", "ingredient-record-001", "MFDS_INGREDIENT_CODE", "I-001", "합성 성분"
            ),
        ),
        components=(component,),
        aliases=(),
    )
    orphaned = replace(members, ingredients=())

    report = validate_catalog_members(orphaned)

    assert report.orphan_count == 1
    assert report.failures[0].reason is CatalogValidationFailureReason.REFERENTIAL_INTEGRITY_INVALID
    assert "합성 성분" not in str(report.failures)


def test_same_component_natural_key_with_different_content_is_rejected() -> None:
    first = CatalogComponentInput(
        source_snapshot_id="snapshot-001",
        product_code_system="MFDS_ITEM_SEQ",
        product_canonical_code="P-001",
        ingredient_code_system="MFDS_INGREDIENT_CODE",
        ingredient_canonical_code="I-001",
        component_role=CatalogComponentRole.ACTIVE_INGREDIENT,
        component_order=1,
        strength_value="10",
        strength_unit="mg",
    )
    second = replace(first, component_order=2, strength_value="20")
    members = build_catalog_members(
        products=(_product("P-001"),),
        ingredients=(
            CatalogIngredientInput(
                "snapshot-001", "ingredient-record-001", "MFDS_INGREDIENT_CODE", "I-001", "합성 성분"
            ),
        ),
        components=(first, second),
        aliases=(),
    )

    report = validate_catalog_members(members)

    assert report.conflict_count == 1
    assert report.failures[0].reason is CatalogValidationFailureReason.MEMBER_CONFLICT


def test_repeated_alias_target_preserves_provenance_and_deduplicates_entries() -> None:
    members = build_catalog_members(
        products=(_product("P-001"),),
        components=(),
        aliases=(
            _alias("P-001", alias_ref="alias-001"),
            _alias("P-001", alias_ref="alias-002"),
        ),
    )

    report = validate_catalog_members(members)

    assert report.is_valid
    assert len(members.aliases) == 2
    assert len(members.search_entries) == 2
