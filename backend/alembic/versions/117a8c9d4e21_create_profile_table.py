"""create profile table

Revision ID: 117a8c9d4e21
Revises: 77585c0c9792
Create Date: 2026-08-28 00:00:00.000000

"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "117a8c9d4e21"
down_revision: str | Sequence[str] | None = "77585c0c9792"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_resource_profiles_backfilled(connection: sa.Connection) -> None:
    resource_tables = (
        "medical_document",
        "prescription",
        "guide",
        "chat_session",
    )

    for table_name in resource_tables:
        resource_table = sa.table(table_name, sa.column("profile_id"))
        missing_count = connection.execute(
            sa.select(sa.func.count()).select_from(resource_table).where(resource_table.c.profile_id.is_(None))
        ).scalar_one()
        if missing_count:
            raise RuntimeError(f"Profile backfill failed: {table_name}.profile_id has {missing_count} missing values.")


def upgrade() -> None:
    connection = op.get_bind()

    op.create_table(
        "profile",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.CHAR(length=36), nullable=False),
        sa.Column(
            "profile_type",
            sa.Enum("SELF", name="profiletype", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("profile_type = 'SELF'", name="chk_profile_type_mvp_self"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "profile_type", name="uq_profile_user_type"),
    )

    user_table = sa.table(
        "user",
        sa.column("id", sa.CHAR(length=36)),
        sa.column("name", sa.String(length=20)),
    )
    profile_table = sa.table(
        "profile",
        sa.column("id", sa.CHAR(length=36)),
        sa.column("user_id", sa.CHAR(length=36)),
        sa.column("profile_type", sa.String(length=30)),
        sa.column("display_name", sa.String(length=100)),
    )

    users = connection.execute(sa.select(user_table.c.id, user_table.c.name)).all()
    if users:
        op.bulk_insert(
            profile_table,
            [
                {
                    "id": str(uuid4()),
                    "user_id": user.id,
                    "profile_type": "SELF",
                    "display_name": user.name,
                }
                for user in users
            ],
        )

    user_count = connection.execute(sa.select(sa.func.count()).select_from(user_table)).scalar_one()
    self_profile_count = connection.execute(
        sa.select(sa.func.count()).select_from(profile_table).where(profile_table.c.profile_type == "SELF")
    ).scalar_one()

    if user_count != self_profile_count:
        raise RuntimeError("Profile backfill failed: every existing user must have exactly one SELF profile.")

    op.add_column("medical_document", sa.Column("profile_id", sa.CHAR(length=36), nullable=True))
    op.create_foreign_key(
        "fk_medical_document_profile_id_profile",
        "medical_document",
        "profile",
        ["profile_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_medical_document_id_profile",
        "medical_document",
        ["id", "profile_id"],
    )
    op.create_index(
        "idx_document_profile_uploaded",
        "medical_document",
        ["profile_id", "uploaded_at", "id"],
        unique=False,
    )

    op.add_column("prescription", sa.Column("profile_id", sa.CHAR(length=36), nullable=True))
    op.create_foreign_key(
        "fk_prescription_profile_id_profile",
        "prescription",
        "profile",
        ["profile_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_prescription_id_profile",
        "prescription",
        ["id", "profile_id"],
    )
    op.create_foreign_key(
        "fk_prescription_document_profile",
        "prescription",
        "medical_document",
        ["document_id", "profile_id"],
        ["id", "profile_id"],
    )
    op.create_index(
        "idx_prescription_profile_created",
        "prescription",
        ["profile_id", "created_at", "id"],
        unique=False,
    )

    op.add_column("guide", sa.Column("profile_id", sa.CHAR(length=36), nullable=True))
    op.create_foreign_key(
        "fk_guide_profile_id_profile",
        "guide",
        "profile",
        ["profile_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_guide_prescription_profile",
        "guide",
        "prescription",
        ["prescription_id", "profile_id"],
        ["id", "profile_id"],
    )
    op.create_index(
        "idx_guide_profile_requested",
        "guide",
        ["profile_id", "requested_at", "id"],
        unique=False,
    )

    op.add_column("chat_session", sa.Column("profile_id", sa.CHAR(length=36), nullable=True))
    op.create_foreign_key(
        "fk_chat_session_profile_id_profile",
        "chat_session",
        "profile",
        ["profile_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_chat_session_prescription_profile",
        "chat_session",
        "prescription",
        ["prescription_id", "profile_id"],
        ["id", "profile_id"],
    )
    op.create_index(
        "idx_chat_session_profile_activity",
        "chat_session",
        ["profile_id", "last_message_at", "id"],
        unique=False,
    )

    op.execute(
        """
        UPDATE medical_document
        SET profile_id = profile.id
        FROM profile
        WHERE medical_document.user_id = profile.user_id
          AND profile.profile_type = 'SELF'
        """
    )
    op.execute(
        """
        UPDATE prescription
        SET profile_id = medical_document.profile_id
        FROM medical_document
        WHERE prescription.document_id = medical_document.id
        """
    )
    op.execute(
        """
        UPDATE guide
        SET profile_id = prescription.profile_id
        FROM prescription
        WHERE guide.prescription_id = prescription.id
        """
    )
    op.execute(
        """
        UPDATE chat_session
        SET profile_id = prescription.profile_id
        FROM prescription
        WHERE chat_session.prescription_id = prescription.id
        """
    )

    _ensure_resource_profiles_backfilled(connection)


def downgrade() -> None:
    op.drop_index("idx_chat_session_profile_activity", table_name="chat_session")
    op.drop_constraint("fk_chat_session_prescription_profile", "chat_session", type_="foreignkey")
    op.drop_constraint("fk_chat_session_profile_id_profile", "chat_session", type_="foreignkey")
    op.drop_column("chat_session", "profile_id")

    op.drop_index("idx_guide_profile_requested", table_name="guide")
    op.drop_constraint("fk_guide_prescription_profile", "guide", type_="foreignkey")
    op.drop_constraint("fk_guide_profile_id_profile", "guide", type_="foreignkey")
    op.drop_column("guide", "profile_id")

    op.drop_index("idx_prescription_profile_created", table_name="prescription")
    op.drop_constraint("fk_prescription_document_profile", "prescription", type_="foreignkey")
    op.drop_constraint("uq_prescription_id_profile", "prescription", type_="unique")
    op.drop_constraint("fk_prescription_profile_id_profile", "prescription", type_="foreignkey")
    op.drop_column("prescription", "profile_id")

    op.drop_index("idx_document_profile_uploaded", table_name="medical_document")
    op.drop_constraint("uq_medical_document_id_profile", "medical_document", type_="unique")
    op.drop_constraint("fk_medical_document_profile_id_profile", "medical_document", type_="foreignkey")
    op.drop_column("medical_document", "profile_id")

    op.drop_table("profile")
