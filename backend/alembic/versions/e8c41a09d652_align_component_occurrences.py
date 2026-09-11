"""Align Component occurrence uniqueness without rewriting stored members.

Revision ID: e8c41a09d652
Revises: 203a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

revision = "e8c41a09d652"
down_revision = "203a1b2c3d4e"
branch_labels = None
depends_on = None

TABLE = "rag_medication_product_component"


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))
    conflicts = connection.scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE} GROUP BY product_id, display_order HAVING count(*) > 1)")
    )
    if conflicts:
        raise RuntimeError("D04 component order conflicts require explicit resolution before migration")
    op.add_column(TABLE, sa.Column("release_profile", sa.String(255), nullable=True))
    op.create_check_constraint(
        "chk_rag_component_release_profile",
        TABLE,
        "release_profile IS NULL OR length(btrim(release_profile)) > 0",
    )
    op.drop_constraint("uq_rag_medication_component_role", TABLE, type_="unique")
    op.create_unique_constraint("uq_rag_medication_component_order", TABLE, ["product_id", "display_order"])
    op.create_index("idx_rag_medication_component_product_ingredient", TABLE, ["product_id", "ingredient_id"])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))
    incompatible = connection.scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {TABLE} WHERE release_profile IS NOT NULL) OR "
            f"EXISTS (SELECT 1 FROM {TABLE} GROUP BY product_id, ingredient_id, component_role HAVING count(*) > 1)"
        )
    )
    if incompatible:
        raise RuntimeError("D04 downgrade would lose component occurrence or release profile data")
    op.drop_index("idx_rag_medication_component_product_ingredient", table_name=TABLE)
    op.drop_constraint("uq_rag_medication_component_order", TABLE, type_="unique")
    op.create_unique_constraint(
        "uq_rag_medication_component_role", TABLE, ["product_id", "ingredient_id", "component_role"]
    )
    op.drop_constraint("chk_rag_component_release_profile", TABLE, type_="check")
    op.drop_column(TABLE, "release_profile")
