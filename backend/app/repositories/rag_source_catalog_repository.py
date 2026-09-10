from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_catalog import (
    RagEntityIdentity,
    RagMedicationAlias,
    RagMedicationAliasReviewStatus,
    RagMedicationAliasTargetType,
    RagMedicationComponentRole,
    RagMedicationIngredient,
    RagMedicationProduct,
    RagMedicationProductComponent,
    RagMedicationRecordStatus,
    RagMedicationSearchEntry,
    RagMedicationSearchEntryType,
)
from app.models.rag_source import (
    RagIngestionRunStatus,
    RagSnapshotVerificationStatus,
    RagSource,
    RagSourceApprovalStatus,
    RagSourceEndpoint,
    RagSourceEndpointLifecycleStatus,
    RagSourceIngestionRun,
    RagSourceLifecycleStatus,
    RagSourceOperation,
    RagSourceSnapshot,
    RagSourceSnapshotVerification,
    RagSourceUsageStatus,
    RagVerificationResultStatus,
)


@dataclass(frozen=True)
class RagSourceCreate:
    source_code: str
    display_name: str
    owner_name: str | None = None
    license_name: str | None = None
    attribution_text: str | None = None
    purpose: str | None = None
    lifecycle_status: RagSourceLifecycleStatus = RagSourceLifecycleStatus.DRAFT


@dataclass(frozen=True)
class RagSourceEndpointCreate:
    source_id: UUID
    endpoint_code: str
    display_name: str
    official_url: str | None = None
    lifecycle_status: RagSourceEndpointLifecycleStatus = RagSourceEndpointLifecycleStatus.DRAFT
    runtime_status: RagSourceUsageStatus = RagSourceUsageStatus.DISABLED
    acquisition_status: RagSourceApprovalStatus = RagSourceApprovalStatus.PENDING


@dataclass(frozen=True)
class RagSourceOperationCreate:
    endpoint_id: UUID
    operation_code: str
    display_name: str
    runtime_status: RagSourceUsageStatus = RagSourceUsageStatus.DISABLED
    acquisition_status: RagSourceApprovalStatus = RagSourceApprovalStatus.PENDING


@dataclass(frozen=True)
class RagSourceSnapshotCreate:
    operation_id: UUID
    source_version: str
    raw_manifest_checksum: str
    canonical_checksum: str
    schema_version: str
    parser_version: str
    normalization_version: str
    canonicalization_spec_version: str
    record_count: int
    rejected_record_count: int
    collected_at: datetime
    verification_status: RagSnapshotVerificationStatus = RagSnapshotVerificationStatus.PENDING
    verified_at: datetime | None = None
    effective_at: datetime | None = None
    supersedes_snapshot_id: UUID | None = None


@dataclass(frozen=True)
class RagSourceIngestionRunCreate:
    operation_id: UUID
    run_group_key: str
    run_status: RagIngestionRunStatus
    attempt_number: int
    started_at: datetime
    snapshot_id: UUID | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    duration_ms: int | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class RagSourceSnapshotVerificationCreate:
    snapshot_id: UUID
    check_name: str
    verification_result: RagVerificationResultStatus
    verified_at: datetime
    details_summary: str | None = None
    verified_by: str | None = None


@dataclass(frozen=True)
class RagMedicationProductCreate:
    entity_identity_id: UUID
    source_snapshot_id: UUID
    source_record_key: str
    code_system: str
    canonical_code: str
    product_name: str
    normalized_product_name: str
    product_status: str
    strength_text: str | None = None
    dosage_form: str | None = None
    manufacturer_name: str | None = None


@dataclass(frozen=True)
class RagMedicationIngredientCreate:
    entity_identity_id: UUID
    source_snapshot_id: UUID
    source_record_key: str
    ingredient_code_system: str
    ingredient_code: str
    ingredient_name: str
    normalized_ingredient_name: str


@dataclass(frozen=True)
class RagMedicationAliasCreate:
    source_snapshot_id: UUID
    target_identity_id: UUID
    target_type: RagMedicationAliasTargetType
    alias_text: str
    normalized_alias_text: str
    alias_source: str
    review_status: RagMedicationAliasReviewStatus
    record_status: RagMedicationRecordStatus
    is_effective: bool


@dataclass(frozen=True)
class RagEntityIdentityCreate:
    entity_type: RagMedicationAliasTargetType
    code_system: str
    canonical_code: str


@dataclass(frozen=True)
class RagMedicationSearchEntryCreate:
    entry_type: RagMedicationSearchEntryType
    product_id: UUID
    product_identity_id: UUID
    normalized_text: str
    alias_id: UUID | None = None


@dataclass(frozen=True)
class RagMedicationProductComponentCreate:
    source_snapshot_id: UUID
    product_id: UUID
    ingredient_id: UUID
    component_role: RagMedicationComponentRole
    display_order: int
    amount_value: Decimal | None = None
    amount_unit: str | None = None
    amount_text: str | None = None


class RagSourceCatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_source_by_code(self, *, source_code: str) -> RagSource | None:
        result = await self.session.execute(select(RagSource).where(RagSource.source_code == source_code))
        return result.scalar_one_or_none()

    async def get_identity(
        self,
        *,
        entity_type: RagMedicationAliasTargetType,
        code_system: str,
        canonical_code: str,
    ) -> RagEntityIdentity | None:
        result = await self.session.execute(
            select(RagEntityIdentity).where(
                RagEntityIdentity.entity_type == entity_type,
                RagEntityIdentity.code_system == code_system,
                RagEntityIdentity.canonical_code == canonical_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_endpoint_by_code(self, *, source_id: UUID, endpoint_code: str) -> RagSourceEndpoint | None:
        result = await self.session.execute(
            select(RagSourceEndpoint).where(
                RagSourceEndpoint.source_id == source_id,
                RagSourceEndpoint.endpoint_code == endpoint_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_operation_by_code(self, *, endpoint_id: UUID, operation_code: str) -> RagSourceOperation | None:
        result = await self.session.execute(
            select(RagSourceOperation).where(
                RagSourceOperation.endpoint_id == endpoint_id,
                RagSourceOperation.operation_code == operation_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_snapshot_by_version(self, *, operation_id: UUID, source_version: str) -> RagSourceSnapshot | None:
        result = await self.session.execute(
            select(RagSourceSnapshot).where(
                RagSourceSnapshot.operation_id == operation_id,
                RagSourceSnapshot.source_version == source_version,
            )
        )
        return result.scalar_one_or_none()

    async def get_product_by_identity(
        self,
        *,
        source_snapshot_id: UUID,
        code_system: str,
        canonical_code: str,
    ) -> RagMedicationProduct | None:
        result = await self.session.execute(
            select(RagMedicationProduct).where(
                RagMedicationProduct.source_snapshot_id == source_snapshot_id,
                RagMedicationProduct.code_system == code_system,
                RagMedicationProduct.canonical_code == canonical_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_product_by_record_key(
        self,
        *,
        source_snapshot_id: UUID,
        source_record_key: str,
    ) -> RagMedicationProduct | None:
        result = await self.session.execute(
            select(RagMedicationProduct).where(
                RagMedicationProduct.source_snapshot_id == source_snapshot_id,
                RagMedicationProduct.source_record_key == source_record_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_ingredients_by_normalized_name(
        self,
        *,
        source_snapshot_id: UUID,
        normalized_ingredient_name: str,
    ) -> list[RagMedicationIngredient]:
        result = await self.session.execute(
            select(RagMedicationIngredient).where(
                RagMedicationIngredient.source_snapshot_id == source_snapshot_id,
                RagMedicationIngredient.normalized_ingredient_name == normalized_ingredient_name,
            )
        )
        return list(result.scalars().all())

    async def get_ingredient_by_record_key(
        self,
        *,
        source_snapshot_id: UUID,
        source_record_key: str,
    ) -> RagMedicationIngredient | None:
        result = await self.session.execute(
            select(RagMedicationIngredient).where(
                RagMedicationIngredient.source_snapshot_id == source_snapshot_id,
                RagMedicationIngredient.source_record_key == source_record_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_ingredient_by_code(
        self,
        *,
        source_snapshot_id: UUID,
        ingredient_code_system: str,
        ingredient_code: str,
    ) -> RagMedicationIngredient | None:
        result = await self.session.execute(
            select(RagMedicationIngredient).where(
                RagMedicationIngredient.source_snapshot_id == source_snapshot_id,
                RagMedicationIngredient.ingredient_code_system == ingredient_code_system,
                RagMedicationIngredient.ingredient_code == ingredient_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_alias(
        self,
        *,
        target_identity_id: UUID,
        source_snapshot_id: UUID,
        normalized_alias_text: str,
        alias_source: str,
    ) -> RagMedicationAlias | None:
        result = await self.session.execute(
            select(RagMedicationAlias).where(
                RagMedicationAlias.target_identity_id == target_identity_id,
                RagMedicationAlias.source_snapshot_id == source_snapshot_id,
                RagMedicationAlias.normalized_alias_text == normalized_alias_text,
                RagMedicationAlias.alias_source == alias_source,
            )
        )
        return result.scalar_one_or_none()

    async def create_identity(self, item: RagEntityIdentityCreate) -> RagEntityIdentity:
        statement = (
            insert(RagEntityIdentity)
            .values(
                entity_type=item.entity_type,
                code_system=item.code_system,
                canonical_code=item.canonical_code,
            )
            .on_conflict_do_nothing(index_elements=["entity_type", "code_system", "canonical_code"])
        )
        await self.session.execute(statement)
        identity = await self.get_identity(
            entity_type=item.entity_type,
            code_system=item.code_system,
            canonical_code=item.canonical_code,
        )
        if identity is None:
            raise RuntimeError("Catalog identity upsert failed")
        return identity

    async def get_component(
        self,
        *,
        product_id: UUID,
        ingredient_id: UUID,
        component_role: RagMedicationComponentRole,
    ) -> RagMedicationProductComponent | None:
        result = await self.session.execute(
            select(RagMedicationProductComponent).where(
                RagMedicationProductComponent.product_id == product_id,
                RagMedicationProductComponent.ingredient_id == ingredient_id,
                RagMedicationProductComponent.component_role == component_role,
            )
        )
        return result.scalar_one_or_none()

    async def create_source(self, item: RagSourceCreate) -> RagSource:
        source = RagSource(
            source_code=item.source_code,
            display_name=item.display_name,
            owner_name=item.owner_name,
            license_name=item.license_name,
            attribution_text=item.attribution_text,
            purpose=item.purpose,
            lifecycle_status=item.lifecycle_status,
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def create_endpoint(self, item: RagSourceEndpointCreate) -> RagSourceEndpoint:
        endpoint = RagSourceEndpoint(
            source_id=item.source_id,
            endpoint_code=item.endpoint_code,
            display_name=item.display_name,
            official_url=item.official_url,
            lifecycle_status=item.lifecycle_status,
            runtime_status=item.runtime_status,
            acquisition_status=item.acquisition_status,
        )
        self.session.add(endpoint)
        await self.session.flush()
        return endpoint

    async def create_operation(self, item: RagSourceOperationCreate) -> RagSourceOperation:
        operation = RagSourceOperation(
            endpoint_id=item.endpoint_id,
            operation_code=item.operation_code,
            display_name=item.display_name,
            runtime_status=item.runtime_status,
            acquisition_status=item.acquisition_status,
        )
        self.session.add(operation)
        await self.session.flush()
        return operation

    async def create_snapshot(self, item: RagSourceSnapshotCreate) -> RagSourceSnapshot:
        snapshot = RagSourceSnapshot(
            operation_id=item.operation_id,
            source_version=item.source_version,
            raw_manifest_checksum=item.raw_manifest_checksum,
            canonical_checksum=item.canonical_checksum,
            schema_version=item.schema_version,
            parser_version=item.parser_version,
            normalization_version=item.normalization_version,
            canonicalization_spec_version=item.canonicalization_spec_version,
            record_count=item.record_count,
            rejected_record_count=item.rejected_record_count,
            verification_status=item.verification_status,
            collected_at=item.collected_at,
            verified_at=item.verified_at,
            effective_at=item.effective_at,
            supersedes_snapshot_id=item.supersedes_snapshot_id,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def create_ingestion_run(self, item: RagSourceIngestionRunCreate) -> RagSourceIngestionRun:
        run = RagSourceIngestionRun(
            operation_id=item.operation_id,
            run_group_key=item.run_group_key,
            snapshot_id=item.snapshot_id,
            run_status=item.run_status,
            attempt_number=item.attempt_number,
            failure_code=item.failure_code,
            failure_message=item.failure_message,
            duration_ms=item.duration_ms,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def create_snapshot_verification(
        self,
        item: RagSourceSnapshotVerificationCreate,
    ) -> RagSourceSnapshotVerification:
        verification = RagSourceSnapshotVerification(
            snapshot_id=item.snapshot_id,
            check_name=item.check_name,
            verification_result=item.verification_result,
            details_summary=item.details_summary,
            verified_by=item.verified_by,
            verified_at=item.verified_at,
        )
        self.session.add(verification)
        await self.session.flush()
        return verification

    async def create_product(self, item: RagMedicationProductCreate) -> RagMedicationProduct:
        await self._require_identity_match(
            identity_id=item.entity_identity_id,
            entity_type=RagMedicationAliasTargetType.PRODUCT,
            code_system=item.code_system,
            canonical_code=item.canonical_code,
        )
        product = RagMedicationProduct(
            source_snapshot_id=item.source_snapshot_id,
            entity_identity_id=item.entity_identity_id,
            source_record_key=item.source_record_key,
            code_system=item.code_system,
            canonical_code=item.canonical_code,
            product_name=item.product_name,
            normalized_product_name=item.normalized_product_name,
            strength_text=item.strength_text,
            dosage_form=item.dosage_form,
            manufacturer_name=item.manufacturer_name,
            product_status=item.product_status,
        )
        self.session.add(product)
        await self.session.flush()
        return product

    async def create_ingredient(self, item: RagMedicationIngredientCreate) -> RagMedicationIngredient:
        await self._require_identity_match(
            identity_id=item.entity_identity_id,
            entity_type=RagMedicationAliasTargetType.INGREDIENT,
            code_system=item.ingredient_code_system,
            canonical_code=item.ingredient_code,
        )
        ingredient = RagMedicationIngredient(
            source_snapshot_id=item.source_snapshot_id,
            entity_identity_id=item.entity_identity_id,
            source_record_key=item.source_record_key,
            ingredient_code_system=item.ingredient_code_system,
            ingredient_code=item.ingredient_code,
            ingredient_name=item.ingredient_name,
            normalized_ingredient_name=item.normalized_ingredient_name,
        )
        self.session.add(ingredient)
        await self.session.flush()
        return ingredient

    async def _require_identity_match(
        self,
        *,
        identity_id: UUID,
        entity_type: RagMedicationAliasTargetType,
        code_system: str,
        canonical_code: str,
    ) -> None:
        result = await self.session.execute(
            select(RagEntityIdentity.id)
            .where(
                RagEntityIdentity.id == identity_id,
                RagEntityIdentity.entity_type == entity_type,
                RagEntityIdentity.code_system == code_system,
                RagEntityIdentity.canonical_code == canonical_code,
            )
            .with_for_update()
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("Catalog member identity does not match its official code")

    async def create_alias(self, item: RagMedicationAliasCreate) -> RagMedicationAlias:
        alias = RagMedicationAlias(
            source_snapshot_id=item.source_snapshot_id,
            target_identity_id=item.target_identity_id,
            target_type=item.target_type,
            alias_text=item.alias_text,
            normalized_alias_text=item.normalized_alias_text,
            alias_source=item.alias_source,
            review_status=item.review_status,
            record_status=item.record_status,
            is_effective=item.is_effective,
        )
        self.session.add(alias)
        await self.session.flush()
        return alias

    async def create_search_entry(self, item: RagMedicationSearchEntryCreate) -> RagMedicationSearchEntry:
        product_result = await self.session.execute(
            select(RagMedicationProduct)
            .where(
                RagMedicationProduct.id == item.product_id,
                RagMedicationProduct.entity_identity_id == item.product_identity_id,
            )
            .with_for_update()
        )
        product = product_result.scalar_one_or_none()
        if product is None or product.product_status != RagMedicationRecordStatus.ACTIVE:
            raise ValueError("Search Entry requires an active Product with the same stable identity")

        if item.entry_type is RagMedicationSearchEntryType.PRODUCT_NAME:
            if item.alias_id is not None or item.normalized_text != product.normalized_product_name:
                raise ValueError("Product-name Search Entry does not match its Product publication")
        elif item.alias_id is None:
            raise ValueError("Approved-alias Search Entry requires an Alias")
        else:
            alias_result = await self.session.execute(
                select(RagMedicationAlias)
                .where(
                    RagMedicationAlias.id == item.alias_id,
                    RagMedicationAlias.target_identity_id == item.product_identity_id,
                    RagMedicationAlias.target_type == RagMedicationAliasTargetType.PRODUCT,
                )
                .with_for_update()
            )
            alias = alias_result.scalar_one_or_none()
            if (
                alias is None
                or alias.review_status is not RagMedicationAliasReviewStatus.APPROVED
                or alias.record_status is not RagMedicationRecordStatus.ACTIVE
                or not alias.is_effective
                or item.normalized_text != alias.normalized_alias_text
            ):
                raise ValueError("Approved-alias Search Entry requires an eligible matching Product Alias")

        entry = RagMedicationSearchEntry(
            entry_type=item.entry_type,
            product_id=item.product_id,
            product_identity_id=item.product_identity_id,
            alias_id=item.alias_id,
            normalized_text=item.normalized_text,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def create_component(self, item: RagMedicationProductComponentCreate) -> RagMedicationProductComponent:
        component = RagMedicationProductComponent(
            source_snapshot_id=item.source_snapshot_id,
            product_id=item.product_id,
            ingredient_id=item.ingredient_id,
            component_role=item.component_role,
            amount_value=item.amount_value,
            amount_unit=item.amount_unit,
            amount_text=item.amount_text,
            display_order=item.display_order,
        )
        self.session.add(component)
        await self.session.flush()
        return component
