"""Preserve independent Component observation sources and historical versions."""

import sqlalchemy as sa
from alembic import op

revision = "166f30415263"
down_revision = "e8c41a09d652"
branch_labels = None
depends_on = None
TABLE = "rag_medication_product_component"


def upgrade() -> None:
    op.execute(sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))
    for name in ("product_source_snapshot_id", "ingredient_source_snapshot_id"):
        op.add_column(TABLE, sa.Column(name, sa.CHAR(36), nullable=True))
    op.add_column(TABLE, sa.Column("observation_json", sa.LargeBinary(), nullable=True))
    # Existing composite FKs prove both references share the recorded Snapshot.
    op.execute(
        sa.text(
            f"UPDATE {TABLE} SET product_source_snapshot_id=source_snapshot_id, ingredient_source_snapshot_id=source_snapshot_id"
        )
    )
    for kind, parent in (("product", "rag_medication_product"), ("ingredient", "rag_medication_ingredient")):
        name = f"{kind}_source_snapshot_id"
        op.alter_column(TABLE, name, nullable=False)
        op.drop_constraint(f"fk_rag_medication_component_{kind}_snapshot", TABLE, type_="foreignkey")
        op.create_foreign_key(
            f"fk_rag_medication_component_{kind}_snapshot",
            TABLE,
            parent,
            [f"{kind}_id", name],
            ["id", "source_snapshot_id"],
        )
    op.drop_constraint("uq_rag_medication_component_order", TABLE, type_="unique")
    op.create_unique_constraint(
        "uq_rag_medication_component_order", TABLE, ["product_id", "source_snapshot_id", "display_order"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))
    if connection.scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {TABLE} WHERE observation_json IS NOT NULL OR product_source_snapshot_id <> source_snapshot_id OR ingredient_source_snapshot_id <> source_snapshot_id) OR EXISTS (SELECT 1 FROM {TABLE} GROUP BY product_id, display_order HAVING count(*) > 1)"
        )
    ):
        raise RuntimeError("D04 downgrade would lose observation provenance or versions")
    op.drop_constraint("uq_rag_medication_component_order", TABLE, type_="unique")
    op.create_unique_constraint("uq_rag_medication_component_order", TABLE, ["product_id", "display_order"])
    for kind, parent in (("product", "rag_medication_product"), ("ingredient", "rag_medication_ingredient")):
        op.drop_constraint(f"fk_rag_medication_component_{kind}_snapshot", TABLE, type_="foreignkey")
        op.create_foreign_key(
            f"fk_rag_medication_component_{kind}_snapshot",
            TABLE,
            parent,
            [f"{kind}_id", "source_snapshot_id"],
            ["id", "source_snapshot_id"],
        )
        op.drop_column(TABLE, f"{kind}_source_snapshot_id")
    op.drop_column(TABLE, "observation_json")
