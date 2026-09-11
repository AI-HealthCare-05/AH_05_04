"""add Catalog set, members, and manifest material

Revision ID: 166b8c9d0e1f
Revises: 166a7b8c9d0e
Create Date: 2026-09-09

D-02 execution provenance remains unresolved. This revision stores a content-addressed
v2 set and does not create or substitute an execution/publication key.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "166b8c9d0e1f"
down_revision: str | Sequence[str] | None = "166a7b8c9d0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_catalog_set",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("catalog_version", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("normalization_version", sa.String(length=100), nullable=False),
        sa.Column("manifest_spec_version", sa.String(length=100), nullable=False),
        sa.Column("envelope_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(btrim(catalog_version)) > 0", name="chk_rag_catalog_set_version_nonblank"),
        sa.CheckConstraint("length(btrim(schema_version)) > 0", name="chk_rag_catalog_set_schema_nonblank"),
        sa.CheckConstraint(
            "length(btrim(normalization_version)) > 0",
            name="chk_rag_catalog_set_normalization_nonblank",
        ),
        sa.CheckConstraint(
            "length(btrim(manifest_spec_version)) > 0",
            name="chk_rag_catalog_set_manifest_spec_nonblank",
        ),
        sa.CheckConstraint(
            "envelope_hash ~ '^[0-9a-f]{64}$'",
            name="chk_rag_catalog_set_envelope_hash",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schema_version",
            "manifest_spec_version",
            "envelope_hash",
            name="uq_rag_catalog_set_envelope",
        ),
    )
    op.create_table(
        "rag_catalog_set_source",
        sa.Column("set_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("source_version", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "length(btrim(source_version)) > 0",
            name="chk_rag_catalog_set_source_version_nonblank",
        ),
        sa.ForeignKeyConstraint(["set_id"], ["rag_catalog_set.id"], name="fk_rag_catalog_set_source_set"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "source_version"],
            ["rag_source_snapshot.id", "rag_source_snapshot.source_version"],
            name="fk_rag_catalog_set_source_snapshot_version",
        ),
        sa.PrimaryKeyConstraint("set_id", "source_snapshot_id"),
    )
    op.create_table(
        "rag_catalog_set_member",
        sa.Column("set_id", sa.CHAR(length=36), nullable=False),
        sa.Column("member_kind", sa.String(length=30), nullable=False),
        sa.Column("member_ref", sa.String(length=100), nullable=False),
        sa.Column("source_snapshot_id", sa.CHAR(length=36), nullable=False),
        sa.Column("product_id", sa.CHAR(length=36), nullable=True),
        sa.Column("ingredient_id", sa.CHAR(length=36), nullable=True),
        sa.Column("component_id", sa.CHAR(length=36), nullable=True),
        sa.Column("alias_id", sa.CHAR(length=36), nullable=True),
        sa.Column("search_entry_id", sa.CHAR(length=36), nullable=True),
        sa.CheckConstraint(
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
        sa.CheckConstraint("length(btrim(member_ref)) > 0", name="chk_rag_catalog_set_member_ref_nonblank"),
        sa.ForeignKeyConstraint(
            ["set_id", "source_snapshot_id"],
            ["rag_catalog_set_source.set_id", "rag_catalog_set_source.source_snapshot_id"],
            name="fk_rag_catalog_set_member_source",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["rag_medication_product.id"], name="fk_rag_catalog_member_product"),
        sa.ForeignKeyConstraint(
            ["ingredient_id"], ["rag_medication_ingredient.id"], name="fk_rag_catalog_member_ingredient"
        ),
        sa.ForeignKeyConstraint(
            ["component_id"], ["rag_medication_product_component.id"], name="fk_rag_catalog_member_component"
        ),
        sa.ForeignKeyConstraint(["alias_id"], ["rag_medication_alias.id"], name="fk_rag_catalog_member_alias"),
        sa.ForeignKeyConstraint(
            ["search_entry_id"], ["rag_medication_search_entry.id"], name="fk_rag_catalog_member_search_entry"
        ),
        sa.PrimaryKeyConstraint("set_id", "member_kind", "member_ref"),
        sa.UniqueConstraint("set_id", "product_id", name="uq_rag_catalog_set_member_product"),
        sa.UniqueConstraint("set_id", "ingredient_id", name="uq_rag_catalog_set_member_ingredient"),
        sa.UniqueConstraint("set_id", "component_id", name="uq_rag_catalog_set_member_component"),
        sa.UniqueConstraint("set_id", "alias_id", name="uq_rag_catalog_set_member_alias"),
        sa.UniqueConstraint("set_id", "search_entry_id", name="uq_rag_catalog_set_member_search_entry"),
    )
    op.create_table(
        "rag_catalog_set_hash",
        sa.Column("set_id", sa.CHAR(length=36), nullable=False),
        sa.Column("hash_kind", sa.String(length=30), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("contract_spec_version", sa.String(length=100), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=50), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "hash_kind IN ('EXPORT_CHECKSUM', 'CATALOG_ENVELOPE')",
            name="chk_rag_catalog_set_hash_kind",
        ),
        sa.CheckConstraint(
            "target IN ('catalog_jsonl', 'envelope_payload')",
            name="chk_rag_catalog_set_hash_target",
        ),
        sa.CheckConstraint("digest ~ '^[0-9a-f]{64}$'", name="chk_rag_catalog_set_hash_digest"),
        sa.ForeignKeyConstraint(["set_id"], ["rag_catalog_set.id"], name="fk_rag_catalog_set_hash_set"),
        sa.PrimaryKeyConstraint("set_id", "hash_kind"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    set_count = connection.execute(sa.text("SELECT count(*) FROM rag_catalog_set")).scalar_one()
    if set_count:
        raise RuntimeError("Cannot downgrade revision 166b8c9d0e1f while Catalog sets exist.")
    for table_name in (
        "rag_catalog_set_hash",
        "rag_catalog_set_member",
        "rag_catalog_set_source",
        "rag_catalog_set",
    ):
        op.drop_table(table_name)
