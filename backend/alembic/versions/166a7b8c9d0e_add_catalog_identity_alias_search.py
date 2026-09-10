"""add Catalog identity, alias state, and search entry foundation

Revision ID: 166a7b8c9d0e
Revises: 201a1b2c3d4e
Create Date: 2026-09-08

D-02 normalization/build execution provenance remains unresolved. This revision
does not create or substitute an execution identifier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "166a7b8c9d0e"
down_revision: str | Sequence[str] | None = "201a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _require_safe_legacy_rows(connection: sa.engine.Connection) -> None:
    alias_count = connection.execute(sa.text("SELECT count(*) FROM rag_medication_alias")).scalar_one()
    if alias_count:
        raise RuntimeError(
            "Cannot infer Catalog Alias source, review status, record status, or effectiveness from is_approved. "
            "Review and migrate legacy Alias rows with approved evidence before revision 166a7b8c9d0e."
        )

    missing_identity_count = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM rag_medication_ingredient
            WHERE ingredient_code_system IS NULL
               OR ingredient_code IS NULL
               OR btrim(ingredient_code_system) = ''
               OR btrim(ingredient_code) = ''
            """
        )
    ).scalar_one()
    if missing_identity_count:
        raise RuntimeError(
            "Cannot infer stable Ingredient identities from names. Resolve legacy Ingredient code gaps "
            "before revision 166a7b8c9d0e."
        )


def upgrade() -> None:
    connection = op.get_bind()
    _require_safe_legacy_rows(connection)

    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.create_table(
        "rag_entity_identity",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("code_system", sa.String(length=50), nullable=False),
        sa.Column("canonical_code", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("entity_type IN ('PRODUCT', 'INGREDIENT')", name="chk_rag_entity_identity_type"),
        sa.CheckConstraint("length(btrim(code_system)) > 0", name="chk_rag_entity_identity_code_system_nonblank"),
        sa.CheckConstraint("length(btrim(canonical_code)) > 0", name="chk_rag_entity_identity_code_nonblank"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_type", "code_system", "canonical_code", name="uq_rag_entity_identity_natural"),
        sa.UniqueConstraint("id", "entity_type", name="uq_rag_entity_identity_id_type"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO rag_entity_identity (id, entity_type, code_system, canonical_code)
            SELECT md5('PRODUCT' || chr(31) || code_system || chr(31) || canonical_code)::uuid::text,
                   'PRODUCT', code_system, canonical_code
            FROM rag_medication_product
            GROUP BY code_system, canonical_code
            UNION ALL
            SELECT md5('INGREDIENT' || chr(31) || ingredient_code_system || chr(31) || ingredient_code)::uuid::text,
                   'INGREDIENT', ingredient_code_system, ingredient_code
            FROM rag_medication_ingredient
            GROUP BY ingredient_code_system, ingredient_code
            """
        )
    )

    for table_name, entity_type in (
        ("rag_medication_product", "PRODUCT"),
        ("rag_medication_ingredient", "INGREDIENT"),
    ):
        op.add_column(table_name, sa.Column("entity_identity_id", sa.CHAR(length=36), nullable=True))
        op.add_column(
            table_name,
            sa.Column("identity_entity_type", sa.String(length=20), nullable=False, server_default=entity_type),
        )

    op.execute(
        sa.text(
            """
            UPDATE rag_medication_product p
            SET entity_identity_id = i.id
            FROM rag_entity_identity i
            WHERE i.entity_type = 'PRODUCT'
              AND i.code_system = p.code_system
              AND i.canonical_code = p.canonical_code
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE rag_medication_ingredient ingredient
            SET entity_identity_id = identity.id
            FROM rag_entity_identity identity
            WHERE identity.entity_type = 'INGREDIENT'
              AND identity.code_system = ingredient.ingredient_code_system
              AND identity.canonical_code = ingredient.ingredient_code
            """
        )
    )
    op.alter_column("rag_medication_product", "entity_identity_id", nullable=False)
    op.alter_column("rag_medication_ingredient", "entity_identity_id", nullable=False)
    op.alter_column("rag_medication_ingredient", "ingredient_code_system", existing_type=sa.String(50), nullable=False)
    op.alter_column("rag_medication_ingredient", "ingredient_code", existing_type=sa.String(100), nullable=False)

    op.create_check_constraint(
        "chk_rag_medication_product_identity_type",
        "rag_medication_product",
        "identity_entity_type = 'PRODUCT'",
    )
    op.create_check_constraint(
        "chk_rag_medication_ingredient_identity_type",
        "rag_medication_ingredient",
        "identity_entity_type = 'INGREDIENT'",
    )
    op.create_foreign_key(
        "fk_rag_medication_product_identity",
        "rag_medication_product",
        "rag_entity_identity",
        ["entity_identity_id", "identity_entity_type"],
        ["id", "entity_type"],
    )
    op.create_foreign_key(
        "fk_rag_medication_ingredient_identity",
        "rag_medication_ingredient",
        "rag_entity_identity",
        ["entity_identity_id", "identity_entity_type"],
        ["id", "entity_type"],
    )
    op.create_unique_constraint(
        "uq_rag_medication_product_id_identity",
        "rag_medication_product",
        ["id", "entity_identity_id", "identity_entity_type"],
    )
    op.create_unique_constraint(
        "uq_rag_medication_ingredient_id_identity",
        "rag_medication_ingredient",
        ["id", "entity_identity_id", "identity_entity_type"],
    )
    op.drop_constraint("uq_rag_medication_ingredient_snapshot_name", "rag_medication_ingredient", type_="unique")
    op.create_unique_constraint(
        "uq_rag_medication_ingredient_snapshot_identity",
        "rag_medication_ingredient",
        ["source_snapshot_id", "entity_identity_id"],
    )
    op.create_index(
        "idx_rag_medication_ingredient_normalized_name",
        "rag_medication_ingredient",
        ["normalized_ingredient_name"],
    )
    op.drop_constraint("fk_rag_medication_alias_product_snapshot", "rag_medication_alias", type_="foreignkey")
    op.drop_constraint("fk_rag_medication_alias_ingredient_snapshot", "rag_medication_alias", type_="foreignkey")
    op.drop_constraint("chk_rag_medication_alias_single_target", "rag_medication_alias", type_="check")
    op.drop_index("uq_rag_medication_alias_product", table_name="rag_medication_alias")
    op.drop_index("uq_rag_medication_alias_ingredient", table_name="rag_medication_alias")
    op.drop_column("rag_medication_alias", "product_id")
    op.drop_column("rag_medication_alias", "ingredient_id")
    op.drop_column("rag_medication_alias", "is_approved")
    op.add_column("rag_medication_alias", sa.Column("target_identity_id", sa.CHAR(length=36), nullable=False))
    op.add_column("rag_medication_alias", sa.Column("alias_source", sa.String(length=100), nullable=False))
    op.add_column("rag_medication_alias", sa.Column("review_status", sa.String(length=20), nullable=False))
    op.add_column("rag_medication_alias", sa.Column("record_status", sa.String(length=20), nullable=False))
    op.add_column("rag_medication_alias", sa.Column("is_effective", sa.Boolean(), nullable=False))
    op.create_foreign_key(
        "fk_rag_medication_alias_target_identity",
        "rag_medication_alias",
        "rag_entity_identity",
        ["target_identity_id", "target_type"],
        ["id", "entity_type"],
    )
    op.create_check_constraint(
        "chk_rag_medication_alias_source_nonblank", "rag_medication_alias", "length(btrim(alias_source)) > 0"
    )
    op.create_check_constraint(
        "chk_rag_medication_alias_review_status",
        "rag_medication_alias",
        "review_status IN ('PENDING', 'APPROVED', 'REJECTED')",
    )
    op.create_check_constraint(
        "chk_rag_medication_alias_record_status",
        "rag_medication_alias",
        "record_status IN ('ACTIVE', 'INACTIVE')",
    )
    op.create_unique_constraint(
        "uq_rag_medication_alias_id_identity",
        "rag_medication_alias",
        ["id", "target_identity_id", "target_type"],
    )
    op.create_unique_constraint(
        "uq_rag_medication_alias_observation",
        "rag_medication_alias",
        ["target_identity_id", "source_snapshot_id", "normalized_alias_text", "alias_source"],
    )
    op.create_index("idx_rag_medication_alias_normalized_text", "rag_medication_alias", ["normalized_alias_text"])
    op.create_index(
        "idx_rag_medication_alias_normalized_text_trgm",
        "rag_medication_alias",
        ["normalized_alias_text"],
        postgresql_using="gin",
        postgresql_ops={"normalized_alias_text": "gin_trgm_ops"},
    )

    op.create_table(
        "rag_medication_search_entry",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("entry_type", sa.String(length=30), nullable=False),
        sa.Column("product_id", sa.CHAR(length=36), nullable=False),
        sa.Column("product_identity_id", sa.CHAR(length=36), nullable=False),
        sa.Column("identity_entity_type", sa.String(length=20), server_default="PRODUCT", nullable=False),
        sa.Column("alias_id", sa.CHAR(length=36), nullable=True),
        sa.Column("normalized_text", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(entry_type = 'PRODUCT_NAME' AND alias_id IS NULL) OR "
            "(entry_type = 'APPROVED_ALIAS' AND alias_id IS NOT NULL)",
            name="chk_rag_medication_search_entry_alias",
        ),
        sa.CheckConstraint(
            "entry_type IN ('PRODUCT_NAME', 'APPROVED_ALIAS')",
            name="chk_rag_medication_search_entry_type",
        ),
        sa.CheckConstraint("identity_entity_type = 'PRODUCT'", name="chk_rag_medication_search_entry_identity_type"),
        sa.CheckConstraint("length(btrim(normalized_text)) > 0", name="chk_rag_medication_search_entry_text_nonblank"),
        sa.ForeignKeyConstraint(
            ["product_id", "product_identity_id", "identity_entity_type"],
            [
                "rag_medication_product.id",
                "rag_medication_product.entity_identity_id",
                "rag_medication_product.identity_entity_type",
            ],
            name="fk_rag_medication_search_entry_product_identity",
        ),
        sa.ForeignKeyConstraint(
            ["alias_id", "product_identity_id", "identity_entity_type"],
            [
                "rag_medication_alias.id",
                "rag_medication_alias.target_identity_id",
                "rag_medication_alias.target_type",
            ],
            name="fk_rag_medication_search_entry_alias_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id", "entry_type", "normalized_text", name="uq_rag_medication_search_entry_product_text"
        ),
    )
    op.create_index(
        "idx_rag_medication_search_entry_normalized_text",
        "rag_medication_search_entry",
        ["normalized_text"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    search_count = connection.execute(sa.text("SELECT count(*) FROM rag_medication_search_entry")).scalar_one()
    alias_count = connection.execute(sa.text("SELECT count(*) FROM rag_medication_alias")).scalar_one()
    if search_count or alias_count:
        raise RuntimeError(
            "Cannot downgrade revision 166a7b8c9d0e while new Catalog Alias or Search Entry rows exist. "
            "The legacy target and approval boolean cannot be reconstructed safely."
        )

    op.drop_table("rag_medication_search_entry")
    op.drop_index("idx_rag_medication_alias_normalized_text_trgm", table_name="rag_medication_alias")
    op.drop_index("idx_rag_medication_alias_normalized_text", table_name="rag_medication_alias")
    op.drop_constraint("uq_rag_medication_alias_observation", "rag_medication_alias", type_="unique")
    op.drop_constraint("uq_rag_medication_alias_id_identity", "rag_medication_alias", type_="unique")
    op.drop_constraint("chk_rag_medication_alias_record_status", "rag_medication_alias", type_="check")
    op.drop_constraint("chk_rag_medication_alias_review_status", "rag_medication_alias", type_="check")
    op.drop_constraint("chk_rag_medication_alias_source_nonblank", "rag_medication_alias", type_="check")
    op.drop_constraint("fk_rag_medication_alias_target_identity", "rag_medication_alias", type_="foreignkey")
    op.drop_column("rag_medication_alias", "is_effective")
    op.drop_column("rag_medication_alias", "record_status")
    op.drop_column("rag_medication_alias", "review_status")
    op.drop_column("rag_medication_alias", "alias_source")
    op.drop_column("rag_medication_alias", "target_identity_id")
    op.add_column(
        "rag_medication_alias", sa.Column("is_approved", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("rag_medication_alias", sa.Column("ingredient_id", sa.CHAR(length=36), nullable=True))
    op.add_column("rag_medication_alias", sa.Column("product_id", sa.CHAR(length=36), nullable=True))
    op.create_check_constraint(
        "chk_rag_medication_alias_single_target",
        "rag_medication_alias",
        "(product_id IS NOT NULL AND ingredient_id IS NULL AND target_type = 'PRODUCT') OR "
        "(product_id IS NULL AND ingredient_id IS NOT NULL AND target_type = 'INGREDIENT')",
    )
    op.create_foreign_key(
        "fk_rag_medication_alias_product_snapshot",
        "rag_medication_alias",
        "rag_medication_product",
        ["product_id", "source_snapshot_id"],
        ["id", "source_snapshot_id"],
    )
    op.create_foreign_key(
        "fk_rag_medication_alias_ingredient_snapshot",
        "rag_medication_alias",
        "rag_medication_ingredient",
        ["ingredient_id", "source_snapshot_id"],
        ["id", "source_snapshot_id"],
    )
    op.create_index(
        "uq_rag_medication_alias_product",
        "rag_medication_alias",
        ["product_id", "normalized_alias_text"],
        unique=True,
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )
    op.create_index(
        "uq_rag_medication_alias_ingredient",
        "rag_medication_alias",
        ["ingredient_id", "normalized_alias_text"],
        unique=True,
        postgresql_where=sa.text("ingredient_id IS NOT NULL"),
    )

    op.drop_index("idx_rag_medication_ingredient_normalized_name", table_name="rag_medication_ingredient")
    op.drop_constraint("uq_rag_medication_ingredient_snapshot_identity", "rag_medication_ingredient", type_="unique")
    op.create_unique_constraint(
        "uq_rag_medication_ingredient_snapshot_name",
        "rag_medication_ingredient",
        ["source_snapshot_id", "normalized_ingredient_name"],
    )
    op.alter_column("rag_medication_ingredient", "ingredient_code", existing_type=sa.String(100), nullable=True)
    op.alter_column("rag_medication_ingredient", "ingredient_code_system", existing_type=sa.String(50), nullable=True)
    for table_name in ("rag_medication_ingredient", "rag_medication_product"):
        op.drop_constraint(f"uq_{table_name}_id_identity", table_name, type_="unique")
        op.drop_constraint(f"fk_{table_name}_identity", table_name, type_="foreignkey")
        op.drop_constraint(f"chk_{table_name}_identity_type", table_name, type_="check")
        op.drop_column(table_name, "identity_entity_type")
        op.drop_column(table_name, "entity_identity_id")
    op.drop_table("rag_entity_identity")
