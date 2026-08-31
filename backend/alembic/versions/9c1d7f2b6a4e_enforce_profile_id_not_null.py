"""enforce profile_id not null and rename medical_document.user_id to uploaded_by

Revision ID: 9c1d7f2b6a4e
Revises: 117a8c9d4e21
Create Date: 2026-08-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1d7f2b6a4e"
down_revision: str | Sequence[str] | None = "117a8c9d4e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESOURCE_TABLES = ("medical_document", "prescription", "guide", "chat_session")


def _ensure_no_missing_profiles(connection: sa.Connection) -> None:
    for table_name in _RESOURCE_TABLES:
        resource_table = sa.table(table_name, sa.column("profile_id"))
        missing_count = connection.execute(
            sa.select(sa.func.count()).select_from(resource_table).where(resource_table.c.profile_id.is_(None))
        ).scalar_one()
        if missing_count:
            raise RuntimeError(f"Cannot enforce NOT NULL: {table_name}.profile_id has {missing_count} missing values.")


def upgrade() -> None:
    connection = op.get_bind()
    _ensure_no_missing_profiles(connection)

    for table_name in _RESOURCE_TABLES:
        op.alter_column(table_name, "profile_id", existing_type=sa.CHAR(length=36), nullable=False)

    op.alter_column("medical_document", "user_id", new_column_name="uploaded_by", existing_type=sa.CHAR(length=36))
    op.execute(
        "ALTER TABLE medical_document RENAME CONSTRAINT medical_document_user_id_fkey "
        "TO medical_document_uploaded_by_fkey"
    )
    op.drop_index("idx_document_user_uploaded", table_name="medical_document")
    op.create_index(
        "idx_document_uploader_uploaded",
        "medical_document",
        ["uploaded_by", "uploaded_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_document_uploader_uploaded", table_name="medical_document")
    op.create_index(
        "idx_document_user_uploaded",
        "medical_document",
        ["user_id", "uploaded_at", "id"],
        unique=False,
    )
    op.execute(
        "ALTER TABLE medical_document RENAME CONSTRAINT medical_document_uploaded_by_fkey "
        "TO medical_document_user_id_fkey"
    )
    op.alter_column("medical_document", "uploaded_by", new_column_name="user_id", existing_type=sa.CHAR(length=36))

    for table_name in _RESOURCE_TABLES:
        op.alter_column(table_name, "profile_id", existing_type=sa.CHAR(length=36), nullable=True)
