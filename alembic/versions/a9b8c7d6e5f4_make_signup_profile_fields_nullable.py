"""make signup profile fields nullable

Revision ID: a9b8c7d6e5f4
Revises: f4c2a1b7d9e3
Create Date: 2026-08-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

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
    op.execute("UPDATE `user` SET gender = 'MALE' WHERE gender IS NULL")
    op.execute("UPDATE `user` SET birthday = '1970-01-01' WHERE birthday IS NULL")
    op.execute("SET @signup_profile_rownum := 0")
    op.execute(
        """
        UPDATE `user`
        SET phone_number = CONCAT('010', LPAD((@signup_profile_rownum := @signup_profile_rownum + 1), 8, '0'))
        WHERE phone_number IS NULL
        ORDER BY created_at, id
        """
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
