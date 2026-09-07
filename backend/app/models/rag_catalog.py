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


class RagMedicationComponentRole(StrEnum):
    ACTIVE_INGREDIENT = "ACTIVE_INGREDIENT"
    EXCIPIENT = "EXCIPIENT"
    UNKNOWN = "UNKNOWN"


class RagMedicationProduct(Base):
    __tablename__ = "rag_medication_product"
    __table_args__ = (
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
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
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
    aliases: Mapped[list["RagMedicationAlias"]] = relationship(
        back_populates="product", overlaps="aliases,ingredient,source_snapshot"
    )
    components: Mapped[list["RagMedicationProductComponent"]] = relationship(
        back_populates="product", overlaps="components,ingredient,source_snapshot"
    )


class RagMedicationIngredient(Base):
    __tablename__ = "rag_medication_ingredient"
    __table_args__ = (
        UniqueConstraint(
            "source_snapshot_id",
            "normalized_ingredient_name",
            name="uq_rag_medication_ingredient_snapshot_name",
        ),
        UniqueConstraint(
            "source_snapshot_id",
            "source_record_key",
            name="uq_rag_medication_ingredient_snapshot_record",
        ),
        UniqueConstraint("id", "source_snapshot_id", name="uq_rag_medication_ingredient_id_snapshot"),
        Index("idx_rag_medication_ingredient_snapshot", "source_snapshot_id"),
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
        CheckConstraint(
            "ingredient_code IS NULL OR length(trim(ingredient_code)) > 0",
            name="chk_rag_medication_ingredient_code_nonblank",
        ),
        CheckConstraint(
            "ingredient_code IS NULL OR ingredient_code_system IS NOT NULL",
            name="chk_rag_medication_ingredient_code_system_required",
        ),
        CheckConstraint(
            "ingredient_code_system IS NULL OR length(trim(ingredient_code_system)) > 0",
            name="chk_rag_medication_ingredient_code_system_nonblank",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    source_record_key: Mapped[str] = mapped_column(String(255), nullable=False)
    ingredient_code_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ingredient_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ingredient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_ingredient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship()
    aliases: Mapped[list["RagMedicationAlias"]] = relationship(
        back_populates="ingredient", overlaps="aliases,product,source_snapshot"
    )
    components: Mapped[list["RagMedicationProductComponent"]] = relationship(
        back_populates="ingredient", overlaps="components,product,source_snapshot"
    )


class RagMedicationAlias(Base):
    __tablename__ = "rag_medication_alias"
    __table_args__ = (
        Index("idx_rag_medication_alias_snapshot", "source_snapshot_id"),
        Index(
            "uq_rag_medication_alias_product",
            "product_id",
            "normalized_alias_text",
            unique=True,
            postgresql_where=text("product_id IS NOT NULL"),
        ),
        Index(
            "uq_rag_medication_alias_ingredient",
            "ingredient_id",
            "normalized_alias_text",
            unique=True,
            postgresql_where=text("ingredient_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["product_id", "source_snapshot_id"],
            ["rag_medication_product.id", "rag_medication_product.source_snapshot_id"],
            name="fk_rag_medication_alias_product_snapshot",
        ),
        ForeignKeyConstraint(
            ["ingredient_id", "source_snapshot_id"],
            ["rag_medication_ingredient.id", "rag_medication_ingredient.source_snapshot_id"],
            name="fk_rag_medication_alias_ingredient_snapshot",
        ),
        CheckConstraint("length(trim(alias_text)) > 0", name="chk_rag_medication_alias_text_nonblank"),
        CheckConstraint(
            "length(trim(normalized_alias_text)) > 0",
            name="chk_rag_medication_alias_normalized_text_nonblank",
        ),
        CheckConstraint(
            "(product_id IS NOT NULL AND ingredient_id IS NULL AND target_type = 'PRODUCT') OR "
            "(product_id IS NULL AND ingredient_id IS NOT NULL AND target_type = 'INGREDIENT')",
            name="chk_rag_medication_alias_single_target",
        ),
        CheckConstraint(
            f"target_type IN ({_sql_in_list(RagMedicationAliasTargetType)})",
            name="chk_rag_medication_alias_target_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    source_snapshot_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("rag_source_snapshot.id"), nullable=False)
    product_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    ingredient_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    target_type: Mapped[RagMedicationAliasTargetType] = mapped_column(
        Enum(RagMedicationAliasTargetType, native_enum=False, length=20),
        nullable=False,
    )
    alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_snapshot: Mapped["RagSourceSnapshot"] = relationship(overlaps="aliases,ingredient,product")
    product: Mapped[RagMedicationProduct | None] = relationship(
        back_populates="aliases", overlaps="aliases,ingredient,source_snapshot"
    )
    ingredient: Mapped[RagMedicationIngredient | None] = relationship(
        back_populates="aliases", overlaps="aliases,product,source_snapshot"
    )


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
