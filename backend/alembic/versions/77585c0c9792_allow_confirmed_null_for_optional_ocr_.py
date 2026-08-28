"""allow confirmed null for optional ocr fields

Revision ID: 77585c0c9792
Revises: 529b2a36b677
Create Date: 2026-08-28 11:45:15.944670

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "77585c0c9792"
down_revision: str | Sequence[str] | None = "529b2a36b677"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "chk_field_confirmation_fields",
        "extracted_field",
        type_="check",
    )

    op.create_check_constraint(
        "chk_field_confirmation_fields",
        "extracted_field",
        "("
        "confirmation_status = 'CONFIRMED' "
        "AND confirmed_at IS NOT NULL "
        "AND ("
        "confirmed_value IS NOT NULL "
        "OR field_type IN ('MEDICATION_STRENGTH', 'DOSE_UNIT', 'TIMING')"
        ")"
        ") "
        "OR ("
        "confirmation_status = 'UNCONFIRMED' "
        "AND confirmed_value IS NULL "
        "AND confirmed_at IS NULL"
        ")",
    )


def downgrade() -> None:
    connection = op.get_bind()

    extracted_field = sa.table(
        "extracted_field",
        sa.column("confirmation_status"),
        sa.column("confirmed_value"),
    )

    confirmed_null_count = connection.execute(
        sa.select(sa.func.count())
        .select_from(extracted_field)
        .where(
            extracted_field.c.confirmation_status == "CONFIRMED",
            extracted_field.c.confirmed_value.is_(None),
        )
    ).scalar_one()

    if confirmed_null_count:
        raise RuntimeError(
            "Cannot downgrade while confirmed optional OCR fields "
            "with null values exist. Back up and migrate the data "
            "through an approved rollback procedure first."
        )

    op.drop_constraint(
        "chk_field_confirmation_fields",
        "extracted_field",
        type_="check",
    )

    op.create_check_constraint(
        "chk_field_confirmation_fields",
        "extracted_field",
        "("
        "confirmation_status = 'CONFIRMED' "
        "AND confirmed_value IS NOT NULL "
        "AND confirmed_at IS NOT NULL"
        ") "
        "OR ("
        "confirmation_status = 'UNCONFIRMED' "
        "AND confirmed_value IS NULL "
        "AND confirmed_at IS NULL"
        ")",
    )
