from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.rag_source import RagSourceSnapshot


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class RagMedicationAliasTargetType(StrEnum):
    PRODUCT = "PRODUCT"
    INGREDIENT = "INGREDIENT"


class RagMedicationAliasReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RagMedicationRecordStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class RagMedicationSearchEntryType(StrEnum):
    PRODUCT_NAME = "PRODUCT_NAME"
    APPROVED_ALIAS = "APPROVED_ALIAS"


class RagMedicationComponentRole(StrEnum):
    ACTIVE_INGREDIENT = "ACTIVE_INGREDIENT"
    EXCIPIENT = "EXCIPIENT"
    UNKNOWN = "UNKNOWN"


class RagCatalogMemberKind(StrEnum):
    PRODUCT = "PRODUCT"
    INGREDIENT = "INGREDIENT"
    COMPONENT = "COMPONENT"
    ALIAS = "ALIAS"
    SEARCH_ENTRY = "SEARCH_ENTRY"


class RagCatalogHashKind(StrEnum):
    EXPORT_CHECKSUM = "EXPORT_CHECKSUM"
    CATALOG_ENVELOPE = "CATALOG_ENVELOPE"


class RagEntityIdentity(Base):
    __tablename__ = "rag_entity_identity"
    __table_args__ = (
        UniqueConstraint("entity_type", "code_system", "canonical_code", name="uq_rag_entity_identity_natural"),
        UniqueConstraint("id", "entity_type", name="uq_rag_entity_identity_id_type"),
        CheckConstraint(
            f"entity_type IN ({_sql_in_list(RagMedicationAliasTargetType)})",
            name="chk_rag_entity_identity_type",
        ),
        CheckConstraint("length(trim(code_system)) > 0", name="chk_rag_entity_identity_code_system_nonblank"),
        CheckConstraint("length(trim(canonical_code)) > 0", name="chk_rag_entity_identity_code_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    entity_type: Mapped[RagMedicationAliasTargetType] = mapped_column(
        Enum(RagMedicationAliasTargetType, native_enum=False, length=20), nullable=False
    )
    code_system: Mapped[str] = mapped_column(String(50), nullable=False)
    canonical_code: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagMedicationProduct(Base):
    catalog_lock_marker: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    __tablename__ = "rag_medication_product"
    __table_args__ = (
        CheckConstraint("catalog_lock_marker = 0", name="chk_rag_medication_product_catalog_lock_marker"),
        UniqueConstraint(
            "source_snapshot_id",
            "code_system",
            "canonical_code",
            name="uq_rag_medication_product_snapshot_identity",
        ),
        UniqueConstraint(
            "source_snapshot_id",
            "source_record_key",
            name="uq_rag_medication_product_snapshot_record",
        ),
        UniqueConstraint("id", "source_snapshot_id", name="uq_rag_medication_product_id_snapshot"),
        UniqueConstraint(
            "id", "entity_identity_id", "identity_entity_type", name="uq_rag_medication_product_id_identity"
        ),
        Index("idx_rag_medication_product_identity", "code_system", "canonical_code"),
        Index("idx_rag_medication_product_snapshot", "source_snapshot_id"),
        CheckConstraint("length(trim(source_record_key)) > 0", name="chk_rag_medication_product_record_key_nonblank"),
        CheckConstraint("length(trim(code_system)) > 0", name="chk_rag_medication_product_code_system_nonblank"),
        CheckConstraint("length(trim(canonical_code)) > 0", name="chk_rag_medication_product_code_nonblank"),
        CheckConstraint("length(trim(product_name)) > 0", name="chk_rag_medication_product_name_nonblank"),
        CheckConstraint(
            "length(trim(normalized_product_name)) > 0",
            name="chk_rag_medication_product_normalized_name_nonblank",
        ),
        CheckConstraint("length(trim(product_status)) > 0", name="chk_rag_medication_product_status_nonblank"),
        CheckConstraint("identity_entity_type = 'PRODUCT'", name="chk_rag_medication_product_identity_type"),
        ForeignKeyConstraint(
            ["entity_identity_id", "identity_entity_type"],
            ["rag_entity_identity.id", "rag_entity_identity.entity_type"],
            name="fk_rag_medication_product_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    entity_identity_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    identity_entity_type: Mapped[RagMedicationAliasTargetType] = mapped_column(
        Enum(RagMedicationAliasTargetType, native_enum=False, length=20),
        nullable=False,
        default=RagMedicationAliasTargetType.PRODUCT,
    )
    source_record_key: Mapped[str] = mapped_column(String(255), nullable=False)
    code_system: Mapped[str] = mapped_column(String(50), nullable=False)
    canonical_code: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    strength_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dosage_form: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manufacturer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship()
    identity: Mapped[RagEntityIdentity] = relationship()
    components: Mapped[list["RagMedicationProductComponent"]] = relationship(
        back_populates="product", overlaps="components,ingredient,source_snapshot"
    )


class RagMedicationIngredient(Base):
    __tablename__ = "rag_medication_ingredient"
    __table_args__ = (
        UniqueConstraint(
            "source_snapshot_id", "entity_identity_id", name="uq_rag_medication_ingredient_snapshot_identity"
        ),
        UniqueConstraint(
            "source_snapshot_id",
            "source_record_key",
            name="uq_rag_medication_ingredient_snapshot_record",
        ),
        UniqueConstraint("id", "source_snapshot_id", name="uq_rag_medication_ingredient_id_snapshot"),
        UniqueConstraint(
            "id", "entity_identity_id", "identity_entity_type", name="uq_rag_medication_ingredient_id_identity"
        ),
        Index("idx_rag_medication_ingredient_snapshot", "source_snapshot_id"),
        Index("idx_rag_medication_ingredient_normalized_name", "normalized_ingredient_name"),
        Index(
            "uq_rag_medication_ingredient_snapshot_code",
            "source_snapshot_id",
            "ingredient_code_system",
            "ingredient_code",
            unique=True,
            postgresql_where=text("ingredient_code IS NOT NULL"),
        ),
        CheckConstraint(
            "length(trim(source_record_key)) > 0", name="chk_rag_medication_ingredient_record_key_nonblank"
        ),
        CheckConstraint("length(trim(ingredient_name)) > 0", name="chk_rag_medication_ingredient_name_nonblank"),
        CheckConstraint(
            "length(trim(normalized_ingredient_name)) > 0",
            name="chk_rag_medication_ingredient_normalized_name_nonblank",
        ),
        CheckConstraint("length(trim(ingredient_code)) > 0", name="chk_rag_medication_ingredient_code_nonblank"),
        CheckConstraint(
            "length(trim(ingredient_code_system)) > 0",
            name="chk_rag_medication_ingredient_code_system_nonblank",
        ),
        CheckConstraint("identity_entity_type = 'INGREDIENT'", name="chk_rag_medication_ingredient_identity_type"),
        ForeignKeyConstraint(
            ["entity_identity_id", "identity_entity_type"],
            ["rag_entity_identity.id", "rag_entity_identity.entity_type"],
            name="fk_rag_medication_ingredient_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    entity_identity_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    identity_entity_type: Mapped[RagMedicationAliasTargetType] = mapped_column(
        Enum(RagMedicationAliasTargetType, native_enum=False, length=20),
        nullable=False,
        default=RagMedicationAliasTargetType.INGREDIENT,
    )
    source_record_key: Mapped[str] = mapped_column(String(255), nullable=False)
    ingredient_code_system: Mapped[str] = mapped_column(String(50), nullable=False)
    ingredient_code: Mapped[str] = mapped_column(String(100), nullable=False)
    ingredient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_ingredient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship()
    identity: Mapped[RagEntityIdentity] = relationship()
    components: Mapped[list["RagMedicationProductComponent"]] = relationship(
        back_populates="ingredient", overlaps="components,product,source_snapshot"
    )


class RagMedicationAlias(Base):
    catalog_lock_marker: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    __tablename__ = "rag_medication_alias"
    __table_args__ = (
        CheckConstraint("catalog_lock_marker = 0", name="chk_rag_medication_alias_catalog_lock_marker"),
        Index("idx_rag_medication_alias_snapshot", "source_snapshot_id"),
        Index("idx_rag_medication_alias_normalized_text", "normalized_alias_text"),
        Index(
            "idx_rag_medication_alias_normalized_text_trgm",
            "normalized_alias_text",
            postgresql_using="gin",
            postgresql_ops={"normalized_alias_text": "gin_trgm_ops"},
        ),
        UniqueConstraint("id", "target_identity_id", "target_type", name="uq_rag_medication_alias_id_identity"),
        UniqueConstraint(
            "target_identity_id",
            "source_snapshot_id",
            "normalized_alias_text",
            "alias_source",
            name="uq_rag_medication_alias_observation",
        ),
        ForeignKeyConstraint(
            ["target_identity_id", "target_type"],
            ["rag_entity_identity.id", "rag_entity_identity.entity_type"],
            name="fk_rag_medication_alias_target_identity",
        ),
        CheckConstraint("length(trim(alias_text)) > 0", name="chk_rag_medication_alias_text_nonblank"),
        CheckConstraint(
            "length(trim(normalized_alias_text)) > 0",
            name="chk_rag_medication_alias_normalized_text_nonblank",
        ),
        CheckConstraint(
            f"target_type IN ({_sql_in_list(RagMedicationAliasTargetType)})",
            name="chk_rag_medication_alias_target_type",
        ),
        CheckConstraint("length(trim(alias_source)) > 0", name="chk_rag_medication_alias_source_nonblank"),
        CheckConstraint(
            f"review_status IN ({_sql_in_list(RagMedicationAliasReviewStatus)})",
            name="chk_rag_medication_alias_review_status",
        ),
        CheckConstraint(
            f"record_status IN ({_sql_in_list(RagMedicationRecordStatus)})",
            name="chk_rag_medication_alias_record_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    target_identity_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    target_type: Mapped[RagMedicationAliasTargetType] = mapped_column(
        Enum(RagMedicationAliasTargetType, native_enum=False, length=20),
        nullable=False,
    )
    alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_source: Mapped[str] = mapped_column(String(100), nullable=False)
    review_status: Mapped[RagMedicationAliasReviewStatus] = mapped_column(
        Enum(RagMedicationAliasReviewStatus, native_enum=False, length=20), nullable=False
    )
    record_status: Mapped[RagMedicationRecordStatus] = mapped_column(
        Enum(RagMedicationRecordStatus, native_enum=False, length=20), nullable=False
    )
    is_effective: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship(overlaps="aliases,ingredient,product")
    target_identity: Mapped[RagEntityIdentity] = relationship()


class RagMedicationSearchEntry(Base):
    __tablename__ = "rag_medication_search_entry"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "entry_type", "normalized_text", name="uq_rag_medication_search_entry_product_text"
        ),
        ForeignKeyConstraint(
            ["product_id", "product_identity_id", "identity_entity_type"],
            [
                "rag_medication_product.id",
                "rag_medication_product.entity_identity_id",
                "rag_medication_product.identity_entity_type",
            ],
            name="fk_rag_medication_search_entry_product_identity",
        ),
        ForeignKeyConstraint(
            ["alias_id", "product_identity_id", "identity_entity_type"],
            [
                "rag_medication_alias.id",
                "rag_medication_alias.target_identity_id",
                "rag_medication_alias.target_type",
            ],
            name="fk_rag_medication_search_entry_alias_identity",
        ),
        CheckConstraint(
            "(entry_type = 'PRODUCT_NAME' AND alias_id IS NULL) OR "
            "(entry_type = 'APPROVED_ALIAS' AND alias_id IS NOT NULL)",
            name="chk_rag_medication_search_entry_alias",
        ),
        CheckConstraint(
            f"entry_type IN ({_sql_in_list(RagMedicationSearchEntryType)})",
            name="chk_rag_medication_search_entry_type",
        ),
        CheckConstraint("identity_entity_type = 'PRODUCT'", name="chk_rag_medication_search_entry_identity_type"),
        CheckConstraint("length(trim(normalized_text)) > 0", name="chk_rag_medication_search_entry_text_nonblank"),
        Index("idx_rag_medication_search_entry_normalized_text", "normalized_text"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    entry_type: Mapped[RagMedicationSearchEntryType] = mapped_column(
        Enum(RagMedicationSearchEntryType, native_enum=False, length=30), nullable=False
    )
    product_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    product_identity_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    identity_entity_type: Mapped[RagMedicationAliasTargetType] = mapped_column(
        Enum(RagMedicationAliasTargetType, native_enum=False, length=20),
        nullable=False,
        default=RagMedicationAliasTargetType.PRODUCT,
    )
    alias_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    normalized_text: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    product: Mapped[RagMedicationProduct] = relationship(overlaps="alias")
    alias: Mapped[RagMedicationAlias | None] = relationship(overlaps="product")


class RagMedicationProductComponent(Base):
    __tablename__ = "rag_medication_product_component"
    __table_args__ = (
        UniqueConstraint("product_id", "ingredient_id", "component_role", name="uq_rag_medication_component_role"),
        ForeignKeyConstraint(
            ["product_id", "source_snapshot_id"],
            ["rag_medication_product.id", "rag_medication_product.source_snapshot_id"],
            name="fk_rag_medication_component_product_snapshot",
        ),
        ForeignKeyConstraint(
            ["ingredient_id", "source_snapshot_id"],
            ["rag_medication_ingredient.id", "rag_medication_ingredient.source_snapshot_id"],
            name="fk_rag_medication_component_ingredient_snapshot",
        ),
        Index("idx_rag_medication_component_snapshot", "source_snapshot_id"),
        Index("idx_rag_medication_component_ingredient", "ingredient_id"),
        CheckConstraint("display_order > 0", name="chk_rag_medication_component_display_order"),
        CheckConstraint(
            "amount_value IS NULL OR amount_value >= 0",
            name="chk_rag_medication_component_amount_value",
        ),
        CheckConstraint(
            "amount_unit IS NULL OR length(trim(amount_unit)) > 0",
            name="chk_rag_medication_component_amount_unit_nonblank",
        ),
        CheckConstraint(
            "amount_text IS NULL OR length(trim(amount_text)) > 0",
            name="chk_rag_medication_component_amount_text_nonblank",
        ),
        CheckConstraint(
            f"component_role IN ({_sql_in_list(RagMedicationComponentRole)})",
            name="chk_rag_medication_component_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    product_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    ingredient_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    component_role: Mapped[RagMedicationComponentRole] = mapped_column(
        Enum(RagMedicationComponentRole, native_enum=False, length=30),
        nullable=False,
        default=RagMedicationComponentRole.ACTIVE_INGREDIENT,
    )
    amount_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    amount_unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    amount_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship(overlaps="components,ingredient,product")
    product: Mapped[RagMedicationProduct] = relationship(
        back_populates="components", overlaps="components,ingredient,source_snapshot"
    )
    ingredient: Mapped[RagMedicationIngredient] = relationship(
        back_populates="components", overlaps="components,product,source_snapshot"
    )


class RagCatalogSet(Base):
    __tablename__ = "rag_catalog_set"
    __table_args__ = (
        UniqueConstraint(
            "schema_version",
            "manifest_spec_version",
            "envelope_hash",
            name="uq_rag_catalog_set_envelope",
        ),
        CheckConstraint("length(trim(catalog_version)) > 0", name="chk_rag_catalog_set_version_nonblank"),
        CheckConstraint("length(trim(schema_version)) > 0", name="chk_rag_catalog_set_schema_nonblank"),
        CheckConstraint(
            "length(trim(normalization_version)) > 0",
            name="chk_rag_catalog_set_normalization_nonblank",
        ),
        CheckConstraint(
            "length(trim(manifest_spec_version)) > 0",
            name="chk_rag_catalog_set_manifest_spec_nonblank",
        ),
        CheckConstraint("envelope_hash ~ '^[0-9a-f]{64}$'", name="chk_rag_catalog_set_envelope_hash"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    catalog_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_spec_version: Mapped[str] = mapped_column(String(100), nullable=False)
    envelope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagCatalogSetSource(Base):
    __tablename__ = "rag_catalog_set_source"
    __table_args__ = (
        CheckConstraint(
            "length(trim(source_version)) > 0",
            name="chk_rag_catalog_set_source_version_nonblank",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_rag_catalog_set_source_snapshot_version",
        ),
    )

    set_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_catalog_set.id"), primary_key=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True)
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)


class RagCatalogSetMember(Base):
    __tablename__ = "rag_catalog_set_member"
    __table_args__ = (
        CheckConstraint(
            "(member_kind = 'PRODUCT' AND product_id IS NOT NULL AND ingredient_id IS NULL "
            "AND component_id IS NULL AND alias_id IS NULL AND search_entry_id IS NULL) OR "
            "(member_kind = 'INGREDIENT' AND product_id IS NULL AND ingredient_id IS NOT NULL "
            "AND component_id IS NULL AND alias_id IS NULL AND search_entry_id IS NULL) OR "
            "(member_kind = 'COMPONENT' AND product_id IS NULL AND ingredient_id IS NULL "
            "AND component_id IS NOT NULL AND alias_id IS NULL AND search_entry_id IS NULL) OR "
            "(member_kind = 'ALIAS' AND product_id IS NULL AND ingredient_id IS NULL "
            "AND component_id IS NULL AND alias_id IS NOT NULL AND search_entry_id IS NULL) OR "
            "(member_kind = 'SEARCH_ENTRY' AND product_id IS NULL AND ingredient_id IS NULL "
            "AND component_id IS NULL AND alias_id IS NULL AND search_entry_id IS NOT NULL)",
            name="chk_rag_catalog_set_member_target",
        ),
        CheckConstraint("length(trim(member_ref)) > 0", name="chk_rag_catalog_set_member_ref_nonblank"),
        UniqueConstraint("set_id", "product_id", name="uq_rag_catalog_set_member_product"),
        UniqueConstraint("set_id", "ingredient_id", name="uq_rag_catalog_set_member_ingredient"),
        UniqueConstraint("set_id", "component_id", name="uq_rag_catalog_set_member_component"),
        UniqueConstraint("set_id", "alias_id", name="uq_rag_catalog_set_member_alias"),
        UniqueConstraint("set_id", "search_entry_id", name="uq_rag_catalog_set_member_search_entry"),
        ForeignKeyConstraint(
            ["set_id", "source_snapshot_id"],
            ["rag_catalog_set_source.set_id", "rag_catalog_set_source.source_snapshot_id"],
            name="fk_rag_catalog_set_member_source",
        ),
    )

    set_id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True)
    member_kind: Mapped[RagCatalogMemberKind] = mapped_column(
        Enum(RagCatalogMemberKind, native_enum=False, length=30), primary_key=True
    )
    member_ref: Mapped[str] = mapped_column(String(100), primary_key=True)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    product_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("rag_medication_product.id"))
    ingredient_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("rag_medication_ingredient.id"))
    component_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("rag_medication_product_component.id"))
    alias_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("rag_medication_alias.id"))
    search_entry_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("rag_medication_search_entry.id"))


class RagCatalogSetHash(Base):
    __tablename__ = "rag_catalog_set_hash"
    __table_args__ = (
        CheckConstraint(
            f"hash_kind IN ({_sql_in_list(RagCatalogHashKind)})",
            name="chk_rag_catalog_set_hash_kind",
        ),
        CheckConstraint(
            "target IN ('catalog_jsonl', 'envelope_payload')",
            name="chk_rag_catalog_set_hash_target",
        ),
        CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="chk_rag_catalog_set_hash_digest"),
    )

    set_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_catalog_set.id"), primary_key=True)
    hash_kind: Mapped[RagCatalogHashKind] = mapped_column(
        Enum(RagCatalogHashKind, native_enum=False, length=30), primary_key=True
    )
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    contract_spec_version: Mapped[str] = mapped_column(String(100), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(50), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
