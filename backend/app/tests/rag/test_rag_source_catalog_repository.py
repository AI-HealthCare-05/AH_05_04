from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.rag_catalog import RagMedicationAliasTargetType, RagMedicationComponentRole
from app.models.rag_source import (
    RagIngestionRunStatus,
    RagSnapshotVerificationStatus,
    RagVerificationResultStatus,
)
from app.repositories.rag_source_catalog_repository import (
    RagMedicationAliasCreate,
    RagMedicationIngredientCreate,
    RagMedicationProductComponentCreate,
    RagMedicationProductCreate,
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceIngestionRunCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
    RagSourceSnapshotVerificationCreate,
)

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
    return await repository.create_snapshot(
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version="api:2026-09-07T00:00:00.000000Z:" + _CHECKSUM,
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
        )
    )


async def test_source_snapshot_catalog_chain_can_be_saved(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)

    product = await repository.create_product(
        RagMedicationProductCreate(
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
            product_id=product.id,
            target_type=RagMedicationAliasTargetType.PRODUCT,
            alias_text="테스트 정",
            normalized_alias_text="테스트정",
            is_approved=True,
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
    assert product_alias.product_id == product.id
    assert product_alias.ingredient_id is None
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
        await repository.get_ingredient_by_normalized_name(
            source_snapshot_id=snapshot.id,
            normalized_ingredient_name="아세트아미노펜",
        )
        == ingredient
    )
    assert await repository.get_product_alias(product_id=product.id, normalized_alias_text="테스트정") == product_alias
    assert (
        await repository.get_component(
            product_id=product.id,
            ingredient_id=ingredient.id,
            component_role=RagMedicationComponentRole.ACTIVE_INGREDIENT,
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
        await repository.create_snapshot(
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
            )
        )


async def test_alias_requires_exactly_one_target(db_session: AsyncSession) -> None:
    repository = RagSourceCatalogRepository(db_session)
    snapshot = await _create_snapshot(repository)
    product = await repository.create_product(
        RagMedicationProductCreate(
            source_snapshot_id=snapshot.id,
            source_record_key="ITEM_SEQ:200012346",
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012346",
            product_name="테스트캡슐",
            normalized_product_name="테스트캡슐",
            product_status="ACTIVE",
        )
    )
    ingredient = await repository.create_ingredient(
        RagMedicationIngredientCreate(
            source_snapshot_id=snapshot.id,
            source_record_key="INGREDIENT:CAFFEINE",
            ingredient_name="카페인",
            normalized_ingredient_name="카페인",
        )
    )

    with pytest.raises(IntegrityError):
        await repository.create_alias(
            RagMedicationAliasCreate(
                source_snapshot_id=snapshot.id,
                product_id=product.id,
                ingredient_id=ingredient.id,
                target_type=RagMedicationAliasTargetType.PRODUCT,
                alias_text="잘못된 별칭",
                normalized_alias_text="잘못된별칭",
            )
        )
