"""Preserve original upload and track the EXIF-normalized OCR/viewer asset.

Revision ID: 809a2b3c4d5e
Revises: 159a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

revision = "809a2b3c4d5e"
down_revision = "159a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("medical_document", sa.Column("normalized_object_key", sa.String(500), nullable=True))
    op.add_column("medical_document", sa.Column("normalized_width", sa.Integer(), nullable=True))
    op.add_column("medical_document", sa.Column("normalized_height", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "chk_document_normalized_image",
        "medical_document",
        "(normalized_object_key IS NULL AND normalized_width IS NULL AND normalized_height IS NULL) OR "
        "(normalized_object_key IS NOT NULL AND length(trim(normalized_object_key)) > 0 "
        "AND normalized_width IS NOT NULL AND normalized_width > 0 "
        "AND normalized_height IS NOT NULL AND normalized_height > 0 "
        "AND file_mime_type IN ('image/jpeg', 'image/png'))",
    )


def downgrade() -> None:
    op.drop_constraint("chk_document_normalized_image", "medical_document", type_="check")
    for name in ("normalized_height", "normalized_width", "normalized_object_key"):
        op.drop_column("medical_document", name)
