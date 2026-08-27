"""add medication normalization fields

Revision ID: b194e393dba7
Revises: b7c4e19a2f3d
Create Date: 2026-08-21 13:43:04.448206

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b194e393dba7"
down_revision: str | Sequence[str] | None = "b7c4e19a2f3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extracted_field",
        sa.Column(
            "normalized_value",
            sa.String(length=1000),
            nullable=True,
        ),
    )
    op.add_column(
        "extracted_field",
        sa.Column(
            "normalization_version",
            sa.String(length=30),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "extracted_field",
        "normalization_version",
    )
    op.drop_column(
        "extracted_field",
        "normalized_value",
    )
