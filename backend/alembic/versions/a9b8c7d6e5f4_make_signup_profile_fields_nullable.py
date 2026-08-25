"""make signup profile fields nullable

Revision ID: a9b8c7d6e5f4
Revises: f4c2a1b7d9e3
Create Date: 2026-08-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import column, func, select, table

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
    # `user`는 예약어라 dialect별 quoting이 다르므로(MySQL 백틱 vs PostgreSQL 더블쿼트),
    # 원시 SQL 문자열 대신 SQLAlchemy Core 표현식으로 dialect가 quoting을 알아서 처리하게 합니다.
    user_table = table("user", column("gender"), column("birthday"), column("phone_number"))
    connection = op.get_bind()
    null_count = connection.execute(
        select(func.count())
        .select_from(user_table)
        .where(user_table.c.gender.is_(None) | user_table.c.birthday.is_(None) | user_table.c.phone_number.is_(None))
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
