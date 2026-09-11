import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_worker.adapters.sqlalchemy_catalog_write_support import (
    CatalogDatabaseBindingError,
    SqlAlchemyCatalogBuildRepository,
    SqlAlchemyCatalogWriteSupport,
)
from ai_worker.tasks.rag.catalog import (
    CandidateAliasReviewStatus,
    CandidateCatalogSourceRef,
    CandidateEntityType,
    CandidateRecordStatus,
    CatalogAliasInput,
    CatalogComponentInput,
    CatalogComponentRole,
    CatalogIngredientInput,
    CatalogProductInput,
    build_catalog_members,
    create_catalog_export,
)
from ai_worker.tasks.rag.catalog.storage import prepare_catalog_storage
from app.core import config
from app.models.rag_catalog import (
    RagCatalogSet,
    RagCatalogSetHash,
    RagCatalogSetMember,
    RagCatalogSetSource,
    RagEntityIdentity,
    RagMedicationAliasReviewStatus,
    RagMedicationAliasTargetType,
    RagMedicationComponentRole,
    RagMedicationRecordStatus,
    RagMedicationSearchEntryType,
)
from app.models.rag_source import (
    RagIngestionRunStatus,
    RagSnapshotVerificationStatus,
    RagSourceSnapshot,
    RagVerificationResultStatus,
)
from app.repositories.rag_source_catalog_repository import (
    RagEntityIdentityCreate,
    RagMedicationAliasCreate,
    RagMedicationIngredientCreate,
    RagMedicationProductComponentCreate,
    RagMedicationProductCreate,
    RagMedicationSearchEntryCreate,
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceIngestionRunCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
    RagSourceSnapshotVerificationCreate,
)
from app.tests.fixtures.source_snapshot import seed_snapshot

_CHECKSUM = "a" * 64
_OTHER_CHECKSUM = "b" * 64


async def _create_snapshot(repository: RagSourceCatalogRepository):
    source = await repository.create_source(
        RagSourceCreate(
            source_code="MFDS_PRODUCT_APPROVAL",
            display_name="MFDS Product Approval",
            owner_name="MFDS",
        )
    )
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(
            source_id=source.id,
            endpoint_code="MFDS_PRODUCT_APPROVAL_API",
            display_name="MFDS Product Approval API",
        )
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(
            endpoint_id=endpoint.id,
            operation_code="LIST_APPROVED_PRODUCTS",
            display_name="List Approved Products",
        )
    )
    snapshot = await seed_snapshot(
        repository,
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version="api:2026-09-07T00:00:00.000000Z:" + _OTHER_CHECKSUM,
            raw_manifest_checksum=_CHECKSUM,
            canonical_checksum=_OTHER_CHECKSUM,
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            verification_status=RagSnapshotVerificationStatus.CURRENT,
            collected_at=datetime.now(config.TIMEZONE),
            verified_at=datetime.now(config.TIMEZONE),
            effective_at=datetime.now(config.TIMEZONE),
        ),
    )

    snapshot.endpoint_receipt_hash = _CHECKSUM
    await repository.session.flush()
    return snapshot


async def test_source_snapshot_catalog_chain_can_be_saved(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)

    product_identity = await repository.create_identity(
        RagEntityIdentityCreate(RagMedicationAliasTargetType.PRODUCT, "MFDS_ITEM_SEQ", "200012345")
    )
    ingredient_identity = await repository.create_identity(
        RagEntityIdentityCreate(RagMedicationAliasTargetType.INGREDIENT, "MFDS_INGREDIENT", "I0001")
    )
    product = await repository.create_product(
        RagMedicationProductCreate(
            entity_identity_id=product_identity.id,
            source_snapshot_id=snapshot.id,
            source_record_key="ITEM_SEQ:200012345",
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012345",
            product_name="테스트정",
            normalized_product_name="테스트정",
            product_status="ACTIVE",
            strength_text="500mg",
            dosage_form="정제",
            manufacturer_name="테스트제약",
        )
    )
    ingredient = await repository.create_ingredient(
        RagMedicationIngredientCreate(
            entity_identity_id=ingredient_identity.id,
            source_snapshot_id=snapshot.id,
            source_record_key="INGREDIENT:ACETAMINOPHEN",
            ingredient_code_system="MFDS_INGREDIENT",
            ingredient_code="I0001",
            ingredient_name="아세트아미노펜",
            normalized_ingredient_name="아세트아미노펜",
        )
    )
    product_alias = await repository.create_alias(
        RagMedicationAliasCreate(
            source_snapshot_id=snapshot.id,
            target_identity_id=product_identity.id,
            target_type=RagMedicationAliasTargetType.PRODUCT,
            alias_text="테스트 정",
            normalized_alias_text="테스트정",
            alias_source="MFDS_PRODUCT_APPROVAL",
            review_status=RagMedicationAliasReviewStatus.APPROVED,
            record_status=RagMedicationRecordStatus.ACTIVE,
            is_effective=True,
        )
    )
    search_entry = await repository.create_search_entry(
        RagMedicationSearchEntryCreate(
            entry_type=RagMedicationSearchEntryType.APPROVED_ALIAS,
            product_id=product.id,
            product_identity_id=product_identity.id,
            alias_id=product_alias.id,
            normalized_text="테스트정",
        )
    )
    component = await repository.create_component(
        RagMedicationProductComponentCreate(
            source_snapshot_id=snapshot.id,
            product_id=product.id,
            ingredient_id=ingredient.id,
            component_role=RagMedicationComponentRole.ACTIVE_INGREDIENT,
            amount_value=Decimal("500"),
            amount_unit="mg",
            amount_text="500mg",
            display_order=1,
        )
    )
    run = await repository.create_ingestion_run(
        RagSourceIngestionRunCreate(
            operation_id=snapshot.operation_id,
            snapshot_id=snapshot.id,
            run_group_key="run-group-1",
            run_status=RagIngestionRunStatus.SUCCEEDED,
            attempt_number=1,
            started_at=datetime.now(config.TIMEZONE),
            finished_at=datetime.now(config.TIMEZONE),
        )
    )
    verification = await repository.create_snapshot_verification(
        RagSourceSnapshotVerificationCreate(
            snapshot_id=snapshot.id,
            check_name="record-count",
            verification_result=RagVerificationResultStatus.PASSED,
            verified_at=datetime.now(config.TIMEZONE),
        )
    )

    assert product.source_snapshot_id == snapshot.id
    assert ingredient.source_snapshot_id == snapshot.id
    assert product_alias.target_identity_id == product_identity.id
    assert search_entry.alias_id == product_alias.id
    assert component.product_id == product.id
    assert component.ingredient_id == ingredient.id
    assert run.snapshot_id == snapshot.id
    assert verification.snapshot_id == snapshot.id

    assert await repository.get_source_by_code(source_code="MFDS_PRODUCT_APPROVAL") is not None
    assert (
        await repository.get_snapshot_by_version(
            operation_id=snapshot.operation_id,
            source_version=snapshot.source_version,
        )
        == snapshot
    )
    assert (
        await repository.get_product_by_identity(
            source_snapshot_id=snapshot.id,
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012345",
        )
        == product
    )
    assert (
        await repository.get_product_by_record_key(
            source_snapshot_id=snapshot.id,
            source_record_key="ITEM_SEQ:200012345",
        )
        == product
    )
    assert await repository.list_ingredients_by_normalized_name(
        source_snapshot_id=snapshot.id,
        normalized_ingredient_name="아세트아미노펜",
    ) == [ingredient]
    assert (
        await repository.get_ingredient_by_record_key(
            source_snapshot_id=snapshot.id,
            source_record_key="INGREDIENT:ACETAMINOPHEN",
        )
        == ingredient
    )
    assert (
        await repository.get_ingredient_by_code(
            source_snapshot_id=snapshot.id,
            ingredient_code_system="MFDS_INGREDIENT",
            ingredient_code="I0001",
        )
        == ingredient
    )
    assert (
        await repository.get_alias(
            target_identity_id=product_identity.id,
            source_snapshot_id=snapshot.id,
            normalized_alias_text="테스트정",
            alias_source="MFDS_PRODUCT_APPROVAL",
        )
        == product_alias
    )
    assert (
        await repository.get_component(
            product_id=product.id,
            display_order=component.display_order,
        )
        == component
    )


async def test_source_code_is_unique(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    await repository.create_source(RagSourceCreate(source_code="MFDS_PRODUCT_APPROVAL", display_name="MFDS"))

    with pytest.raises(IntegrityError):
        await repository.create_source(RagSourceCreate(source_code="MFDS_PRODUCT_APPROVAL", display_name="MFDS again"))


async def test_snapshot_checksum_must_be_sha256_length(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    source = await repository.create_source(RagSourceCreate(source_code="MFDS_DUR", display_name="MFDS DUR"))
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(source_id=source.id, endpoint_code="MFDS_DUR_API", display_name="MFDS DUR API")
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="LIST_DUR", display_name="List DUR")
    )

    with pytest.raises(IntegrityError):
        await seed_snapshot(
            repository,
            RagSourceSnapshotCreate(
                operation_id=operation.id,
                source_version="api:bad-checksum",
                raw_manifest_checksum="short",
                canonical_checksum=_CHECKSUM,
                schema_version="schema-v1",
                parser_version="parser-v1",
                normalization_version="normalization-v1",
                canonicalization_spec_version="canonical-v1",
                record_count=0,
                rejected_record_count=0,
                collected_at=datetime.now(config.TIMEZONE),
            ),
        )


async def test_alias_target_type_must_match_stable_identity(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    product_identity = await repository.create_identity(
        RagEntityIdentityCreate(RagMedicationAliasTargetType.PRODUCT, "MFDS_ITEM_SEQ", "200012346")
    )

    with pytest.raises(IntegrityError):
        await repository.create_alias(
            RagMedicationAliasCreate(
                source_snapshot_id=snapshot.id,
                target_identity_id=product_identity.id,
                target_type=RagMedicationAliasTargetType.INGREDIENT,
                alias_text="잘못된 별칭",
                normalized_alias_text="잘못된별칭",
                alias_source="SYNTHETIC",
                review_status=RagMedicationAliasReviewStatus.PENDING,
                record_status=RagMedicationRecordStatus.ACTIVE,
                is_effective=True,
            )
        )


async def test_only_one_current_snapshot_per_operation(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)

    with pytest.raises(IntegrityError):
        await seed_snapshot(
            repository,
            RagSourceSnapshotCreate(
                operation_id=snapshot.operation_id,
                source_version="api:2026-09-07T00:01:00.000000Z:" + _OTHER_CHECKSUM,
                raw_manifest_checksum=_OTHER_CHECKSUM,
                canonical_checksum=_CHECKSUM,
                schema_version="schema-v1",
                parser_version="parser-v1",
                normalization_version="normalization-v1",
                canonicalization_spec_version="canonical-v1",
                record_count=1,
                rejected_record_count=0,
                verification_status=RagSnapshotVerificationStatus.CURRENT,
                collected_at=datetime.now(config.TIMEZONE),
            ),
        )


async def test_rejected_record_count_cannot_exceed_record_count(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    source = await repository.create_source(RagSourceCreate(source_code="MFDS_DUR", display_name="MFDS DUR"))
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(source_id=source.id, endpoint_code="MFDS_DUR_API", display_name="MFDS DUR API")
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="LIST_DUR", display_name="List DUR")
    )

    with pytest.raises(IntegrityError):
        await seed_snapshot(
            repository,
            RagSourceSnapshotCreate(
                operation_id=operation.id,
                source_version="api:bad-count:" + _CHECKSUM,
                raw_manifest_checksum=_CHECKSUM,
                canonical_checksum=_OTHER_CHECKSUM,
                schema_version="schema-v1",
                parser_version="parser-v1",
                normalization_version="normalization-v1",
                canonicalization_spec_version="canonical-v1",
                record_count=1,
                rejected_record_count=2,
                collected_at=datetime.now(config.TIMEZONE),
            ),
        )


async def test_alias_can_observe_product_identity_from_another_snapshot(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    newer_snapshot = await seed_snapshot(
        repository,
        RagSourceSnapshotCreate(
            operation_id=snapshot.operation_id,
            source_version="api:2026-09-07T00:02:00.000000Z:" + _OTHER_CHECKSUM,
            raw_manifest_checksum=_OTHER_CHECKSUM,
            canonical_checksum=_CHECKSUM,
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            verification_status=RagSnapshotVerificationStatus.STALE,
            collected_at=datetime.now(config.TIMEZONE),
        ),
    )
    product_identity = await repository.create_identity(
        RagEntityIdentityCreate(RagMedicationAliasTargetType.PRODUCT, "MFDS_ITEM_SEQ", "200012347")
    )
    product = await repository.create_product(
        RagMedicationProductCreate(
            entity_identity_id=product_identity.id,
            source_snapshot_id=snapshot.id,
            source_record_key="ITEM_SEQ:200012347",
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012347",
            product_name="스냅샷제품",
            normalized_product_name="스냅샷제품",
            product_status="ACTIVE",
        )
    )

    alias = await repository.create_alias(
        RagMedicationAliasCreate(
            source_snapshot_id=newer_snapshot.id,
            target_identity_id=product_identity.id,
            target_type=RagMedicationAliasTargetType.PRODUCT,
            alias_text="다른 스냅샷 별칭",
            normalized_alias_text="다른스냅샷별칭",
            alias_source="SYNTHETIC",
            review_status=RagMedicationAliasReviewStatus.APPROVED,
            record_status=RagMedicationRecordStatus.ACTIVE,
            is_effective=True,
        )
    )

    assert alias.source_snapshot_id == newer_snapshot.id
    assert alias.target_identity_id == product.entity_identity_id


async def test_search_entry_rejects_ineligible_alias(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    product_identity = await repository.create_identity(
        RagEntityIdentityCreate(RagMedicationAliasTargetType.PRODUCT, "MFDS_ITEM_SEQ", "200012349")
    )
    product = await repository.create_product(
        RagMedicationProductCreate(
            entity_identity_id=product_identity.id,
            source_snapshot_id=snapshot.id,
            source_record_key="ITEM_SEQ:200012349",
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012349",
            product_name="미승인별칭제품",
            normalized_product_name="미승인별칭제품",
            product_status="ACTIVE",
        )
    )
    pending_alias = await repository.create_alias(
        RagMedicationAliasCreate(
            source_snapshot_id=snapshot.id,
            target_identity_id=product_identity.id,
            target_type=RagMedicationAliasTargetType.PRODUCT,
            alias_text="검토 중 별칭",
            normalized_alias_text="검토중별칭",
            alias_source="SYNTHETIC",
            review_status=RagMedicationAliasReviewStatus.PENDING,
            record_status=RagMedicationRecordStatus.ACTIVE,
            is_effective=True,
        )
    )

    with pytest.raises(ValueError, match="eligible matching Product Alias"):
        await repository.create_search_entry(
            RagMedicationSearchEntryCreate(
                entry_type=RagMedicationSearchEntryType.APPROVED_ALIAS,
                product_id=product.id,
                product_identity_id=product_identity.id,
                alias_id=pending_alias.id,
                normalized_text=pending_alias.normalized_alias_text,
            )
        )


@pytest.mark.parametrize("member_kind", ("PRODUCT", "INGREDIENT"))
async def test_catalog_member_rejects_identity_with_different_official_code(
    db_session: AsyncSession,
    member_kind: str,
) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    entity_type = (
        RagMedicationAliasTargetType.PRODUCT if member_kind == "PRODUCT" else RagMedicationAliasTargetType.INGREDIENT
    )
    identity = await repository.create_identity(RagEntityIdentityCreate(entity_type, "MFDS_TEST_CODE", "EXPECTED"))

    with pytest.raises(ValueError, match="identity does not match"):
        if member_kind == "PRODUCT":
            await repository.create_product(
                RagMedicationProductCreate(
                    entity_identity_id=identity.id,
                    source_snapshot_id=snapshot.id,
                    source_record_key="ITEM_SEQ:MISMATCH",
                    code_system="MFDS_TEST_CODE",
                    canonical_code="DIFFERENT",
                    product_name="식별자불일치제품",
                    normalized_product_name="식별자불일치제품",
                    product_status="ACTIVE",
                )
            )
        else:
            await repository.create_ingredient(
                RagMedicationIngredientCreate(
                    entity_identity_id=identity.id,
                    source_snapshot_id=snapshot.id,
                    source_record_key="INGREDIENT:MISMATCH",
                    ingredient_code_system="MFDS_TEST_CODE",
                    ingredient_code="DIFFERENT",
                    ingredient_name="식별자불일치성분",
                    normalized_ingredient_name="식별자불일치성분",
                )
            )


async def test_catalog_write_support_binds_source_and_upserts_identity_once(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012350",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012350",
                product_name="어댑터결속제품",
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        components=(),
        aliases=(),
    )
    artifacts = create_catalog_export(
        catalog_version="catalog-adapter-binding-v1",
        source_refs=(CandidateCatalogSourceRef(str(snapshot.id), snapshot.source_version),),
        members=members,
    )
    plan = prepare_catalog_storage(members=members, artifacts=artifacts)
    support = SqlAlchemyCatalogWriteSupport(db_session)

    first = await support.bind(plan)
    second = await support.bind(plan)

    assert first.source_snapshot_ids == {str(snapshot.id): snapshot.id}
    assert first.identity_ids == second.identity_ids
    assert set(first.identity_ids) == set(plan.identities)


async def test_catalog_write_support_rejects_source_version_mismatch(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012351",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012351",
                product_name="출처불일치제품",
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        components=(),
        aliases=(),
    )
    artifacts = create_catalog_export(
        catalog_version="catalog-adapter-binding-v1",
        source_refs=(CandidateCatalogSourceRef(str(snapshot.id), "다른-source-version"),),
        members=members,
    )
    plan = prepare_catalog_storage(members=members, artifacts=artifacts)

    with pytest.raises(CatalogDatabaseBindingError):
        await SqlAlchemyCatalogWriteSupport(db_session).bind(plan)


async def test_catalog_write_support_stages_compatible_members_idempotently(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012352",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012352",
                product_name="어댑터통합제품",
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        ingredients=(
            CatalogIngredientInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="INGREDIENT:I0352",
                code_system="MFDS_INGREDIENT_CODE",
                canonical_code="I0352",
                ingredient_name="통합성분",
            ),
        ),
        components=(
            CatalogComponentInput(
                source_snapshot_id=str(snapshot.id),
                product_code_system="MFDS_ITEM_SEQ",
                product_canonical_code="200012352",
                ingredient_code_system="MFDS_INGREDIENT_CODE",
                ingredient_canonical_code="I0352",
                component_role=CatalogComponentRole.ACTIVE_INGREDIENT,
                component_order=1,
                strength_value="500",
                strength_unit="mg",
            ),
        ),
        aliases=(
            CatalogAliasInput(
                source_snapshot_id=str(snapshot.id),
                source_alias_ref="ALIAS:200012352:1",
                target_type=CandidateEntityType.PRODUCT,
                target_code_system="MFDS_ITEM_SEQ",
                target_canonical_code="200012352",
                alias_source="MFDS_PRODUCT_APPROVAL",
                alias_text="어댑터 통합정",
                review_status=CandidateAliasReviewStatus.APPROVED,
                status=CandidateRecordStatus.ACTIVE,
                is_effective=True,
            ),
        ),
    )
    artifacts = create_catalog_export(
        catalog_version="catalog-adapter-staging-v1",
        source_refs=(CandidateCatalogSourceRef(str(snapshot.id), snapshot.source_version),),
        members=members,
    )
    plan = prepare_catalog_storage(members=members, artifacts=artifacts)
    support = SqlAlchemyCatalogWriteSupport(db_session)

    first = await support.stage_compatible_members(plan)
    second = await support.stage_compatible_members(plan)

    assert first.product_ids == second.product_ids
    assert first.ingredient_ids == second.ingredient_ids
    assert first.alias_ids == second.alias_ids
    assert first.component_ids == second.component_ids
    assert first.search_entry_ids == second.search_entry_ids
    assert len(first.product_ids) == len(first.ingredient_ids) == len(first.alias_ids) == len(first.component_ids) == 1
    assert len(first.search_entry_ids) == 2
    assert first.set_id == second.set_id


@pytest.mark.parametrize(
    ("record_kind", "field_name", "invalid_value"),
    (
        ("SEARCH_ENTRY", "normalized_text", "다른정규화값"),
        ("ALIAS", "review_status", "PENDING"),
    ),
)
async def test_catalog_write_support_revalidates_search_entry_eligibility(
    db_session: AsyncSession,
    record_kind: str,
    field_name: str,
    invalid_value: str,
) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012355",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012355",
                product_name="검색재검증제품",
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        components=(),
        aliases=(
            CatalogAliasInput(
                source_snapshot_id=str(snapshot.id),
                source_alias_ref="ALIAS:200012355:1",
                target_type=CandidateEntityType.PRODUCT,
                target_code_system="MFDS_ITEM_SEQ",
                target_canonical_code="200012355",
                alias_source="MFDS_PRODUCT_APPROVAL",
                alias_text="검색 재검증정",
                review_status=CandidateAliasReviewStatus.APPROVED,
                status=CandidateRecordStatus.ACTIVE,
                is_effective=True,
            ),
        ),
    )
    artifacts = create_catalog_export(
        catalog_version="catalog-search-revalidation-v1",
        source_refs=(CandidateCatalogSourceRef(str(snapshot.id), snapshot.source_version),),
        members=members,
    )
    plan = prepare_catalog_storage(members=members, artifacts=artifacts)
    changed_rows = []
    changed = False
    for row in plan.rows:
        if not changed and row.kind == record_kind:
            record = json.loads(row.canonical_record)
            record[field_name] = invalid_value
            changed_rows.append(
                replace(
                    row,
                    canonical_record=json.dumps(
                        record,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )
            )
            changed = True
        else:
            changed_rows.append(row)
    assert changed

    with pytest.raises(CatalogDatabaseBindingError):
        await SqlAlchemyCatalogWriteSupport(db_session).stage_compatible_members(
            replace(plan, rows=tuple(changed_rows))
        )


async def test_catalog_set_verification_compares_exact_hash_material(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012356",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012356",
                product_name="셋검증제품",
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        components=(),
        aliases=(),
    )
    artifacts = create_catalog_export(
        catalog_version="catalog-exact-set-verification-v1",
        source_refs=(CandidateCatalogSourceRef(str(snapshot.id), snapshot.source_version),),
        members=members,
    )
    plan = prepare_catalog_storage(members=members, artifacts=artifacts)
    support = SqlAlchemyCatalogWriteSupport(db_session)
    staged = await support.stage_compatible_members(plan)
    assert staged.set_id is not None
    await db_session.execute(
        update(RagCatalogSetHash).where(RagCatalogSetHash.set_id == staged.set_id).values(canonical_bytes=b"tampered")
    )

    with pytest.raises(CatalogDatabaseBindingError):
        await support.verify_set(staged.set_id, plan, staged)


async def test_catalog_build_repository_rejects_caller_owned_transaction(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012354",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012354",
                product_name="전체저장제품",
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        components=(),
        aliases=(),
    )
    artifacts = create_catalog_export(
        catalog_version="catalog-save-build-v1",
        source_refs=(CandidateCatalogSourceRef(str(snapshot.id), snapshot.source_version),),
        members=members,
    )
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    catalog_repository = SqlAlchemyCatalogBuildRepository(session_factory)

    with pytest.raises(CatalogDatabaseBindingError):
        await catalog_repository.save_build(members=members, artifacts=artifacts)

    assert db_session.in_transaction()
    for model in (RagEntityIdentity, RagCatalogSet, RagCatalogSetSource, RagCatalogSetMember, RagCatalogSetHash):
        assert (await db_session.execute(select(func.count()).select_from(model))).scalar_one() == 0


async def test_catalog_write_support_rolls_back_all_members_on_unsupported_row(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="ITEM_SEQ:200012353",
                code_system="MFDS_ITEM_SEQ",
                canonical_code="200012353",
                product_name="롤백검증제품",
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        ingredients=(
            CatalogIngredientInput(
                source_snapshot_id=str(snapshot.id),
                source_record_key="INGREDIENT:I0353",
                code_system="MFDS_INGREDIENT_CODE",
                canonical_code="I0353",
                ingredient_name="미지원비활성성분",
                status=CandidateRecordStatus.INACTIVE,
            ),
        ),
        components=(),
        aliases=(),
    )
    artifacts = create_catalog_export(
        catalog_version="catalog-adapter-rollback-v1",
        source_refs=(CandidateCatalogSourceRef(str(snapshot.id), snapshot.source_version),),
        members=members,
    )
    plan = prepare_catalog_storage(members=members, artifacts=artifacts)

    with pytest.raises(CatalogDatabaseBindingError):
        await SqlAlchemyCatalogWriteSupport(db_session).stage_compatible_members(plan)

    assert (
        await repository.get_product_by_identity(
            source_snapshot_id=snapshot.id,
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012353",
        )
        is None
    )
    assert (
        await repository.get_identity(
            entity_type=RagMedicationAliasTargetType.PRODUCT,
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012353",
        )
        is None
    )


async def test_component_product_and_ingredient_must_use_same_snapshot(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    newer_snapshot = await seed_snapshot(
        repository,
        RagSourceSnapshotCreate(
            operation_id=snapshot.operation_id,
            source_version="api:2026-09-07T00:03:00.000000Z:" + _OTHER_CHECKSUM,
            raw_manifest_checksum=_OTHER_CHECKSUM,
            canonical_checksum=_CHECKSUM,
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            verification_status=RagSnapshotVerificationStatus.STALE,
            collected_at=datetime.now(config.TIMEZONE),
        ),
    )
    product_identity = await repository.create_identity(
        RagEntityIdentityCreate(RagMedicationAliasTargetType.PRODUCT, "MFDS_ITEM_SEQ", "200012348")
    )
    ingredient_identity = await repository.create_identity(
        RagEntityIdentityCreate(RagMedicationAliasTargetType.INGREDIENT, "MFDS_INGREDIENT", "SNAPSHOT_MISMATCH")
    )
    product = await repository.create_product(
        RagMedicationProductCreate(
            entity_identity_id=product_identity.id,
            source_snapshot_id=snapshot.id,
            source_record_key="ITEM_SEQ:200012348",
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012348",
            product_name="컴포넌트제품",
            normalized_product_name="컴포넌트제품",
            product_status="ACTIVE",
        )
    )
    ingredient = await repository.create_ingredient(
        RagMedicationIngredientCreate(
            entity_identity_id=ingredient_identity.id,
            source_snapshot_id=newer_snapshot.id,
            source_record_key="INGREDIENT:SNAPSHOT_MISMATCH",
            ingredient_code_system="MFDS_INGREDIENT",
            ingredient_code="SNAPSHOT_MISMATCH",
            ingredient_name="다른스냅샷성분",
            normalized_ingredient_name="다른스냅샷성분",
        )
    )

    with pytest.raises(IntegrityError):
        await repository.create_component(
            RagMedicationProductComponentCreate(
                source_snapshot_id=snapshot.id,
                product_id=product.id,
                ingredient_id=ingredient.id,
                component_role=RagMedicationComponentRole.ACTIVE_INGREDIENT,
                display_order=1,
            )
        )


async def test_ingestion_attempt_number_is_unique_per_run_group(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    started_at = datetime.now(config.TIMEZONE)

    await repository.create_ingestion_run(
        RagSourceIngestionRunCreate(
            operation_id=snapshot.operation_id,
            run_group_key="retry-group-a",
            run_status=RagIngestionRunStatus.FAILED,
            attempt_number=1,
            started_at=started_at,
        )
    )

    next_run = await repository.create_ingestion_run(
        RagSourceIngestionRunCreate(
            operation_id=snapshot.operation_id,
            run_group_key="retry-group-b",
            run_status=RagIngestionRunStatus.RUNNING,
            attempt_number=1,
            started_at=started_at,
        )
    )

    assert next_run.attempt_number == 1

    with pytest.raises(IntegrityError):
        await repository.create_ingestion_run(
            RagSourceIngestionRunCreate(
                operation_id=snapshot.operation_id,
                run_group_key="retry-group-a",
                run_status=RagIngestionRunStatus.FAILED,
                attempt_number=1,
                started_at=started_at,
            )
        )


@pytest.mark.parametrize(
    "status,timestamp_field",
    [
        (RagSnapshotVerificationStatus.CURRENT, None),
        (RagSnapshotVerificationStatus.FAILED, None),
        (RagSnapshotVerificationStatus.STALE, None),
        (RagSnapshotVerificationStatus.PENDING, "verified_at"),
        (RagSnapshotVerificationStatus.PENDING, "effective_at"),
    ],
)
async def test_snapshot_creation_requires_pending_without_timestamps(db_session, status, timestamp_field):
    item = RagSourceSnapshotCreate(
        operation_id=uuid4(),
        source_version="synthetic:invalid-state",
        raw_manifest_checksum=_CHECKSUM,
        canonical_checksum=_CHECKSUM,
        schema_version="1",
        parser_version="1",
        normalization_version="1",
        canonicalization_spec_version="1",
        record_count=0,
        rejected_record_count=0,
        collected_at=datetime.now(config.TIMEZONE),
        verification_status=status,
        **({timestamp_field: datetime.now(config.TIMEZONE)} if timestamp_field else {}),
    )
    repository = RagSourceCatalogRepository(db_session)
    with pytest.raises(ValueError, match="must start PENDING"):
        await repository.create_snapshot(item)
    # Failure precedes even the foreign-key lookup and keeps the transaction usable.
    assert await db_session.scalar(select(func.count()).select_from(RagSourceSnapshot)) == 0
