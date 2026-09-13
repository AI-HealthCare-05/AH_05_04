"""Allow Catalog row locks without granting payload UPDATE (PR #372)."""

import sqlalchemy as sa
from alembic import op

revision = "166f20314253"
down_revision = "166e1f203142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("rag_medication_product", "rag_medication_alias"):
        op.add_column(table, sa.Column("catalog_lock_marker", sa.Integer(), server_default="0", nullable=False))
        op.create_check_constraint(f"chk_{table}_catalog_lock_marker", table, "catalog_lock_marker = 0")


def downgrade() -> None:
    raise RuntimeError("Catalog Writer lock isolation requires a reviewed forward fix")
