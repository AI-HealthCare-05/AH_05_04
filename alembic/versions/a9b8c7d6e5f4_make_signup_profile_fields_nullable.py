"""make signup profile fields nullable

Revision ID: a9b8c7d6e5f4
Revises: f4c2a1b7d9e3
Create Date: 2026-08-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: str | Sequence[str] | None = "f4c2a1b7d9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "user",
        "gender",
        existing_type=sa.Enum("MALE", "FEMALE", name="gender", native_enum=False, length=10),
        nullable=True,
    )
    op.alter_column(
        "user",
        "birthday",
        existing_type=sa.Date(),
        nullable=True,
    )
    op.alter_column(
        "user",
        "phone_number",
        existing_type=sa.String(length=20),
        nullable=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    null_count = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM `user`
            WHERE gender IS NULL OR birthday IS NULL OR phone_number IS NULL
            """
        )
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            "Cannot downgrade while signup profile fields contain NULL values. "
            "Backfill gender, birthday, and phone_number with approved user-provided values first."
        )
    op.alter_column(
        "user",
        "phone_number",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.alter_column(
        "user",
        "birthday",
        existing_type=sa.Date(),
        nullable=False,
    )
    op.alter_column(
        "user",
        "gender",
        existing_type=sa.Enum("MALE", "FEMALE", name="gender", native_enum=False, length=10),
        nullable=False,
    )
