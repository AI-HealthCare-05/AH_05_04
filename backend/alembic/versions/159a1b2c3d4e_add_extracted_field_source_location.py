"""Add extracted_field source location columns (REQ-OCR-014).

Revision ID: 159a1b2c3d4e
Revises: 780a1b2c3d4e
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op

revision = "159a1b2c3d4e"
down_revision = "780a1b2c3d4e"
branch_labels = None
depends_on = None

_TABLE_NAME = "extracted_field"
_PAGE_COLUMN = "source_page"
_BBOX_COLUMNS = (
    "source_bbox_x",
    "source_bbox_y",
    "source_bbox_width",
    "source_bbox_height",
)
_PAGE_CONSTRAINT = "chk_field_source_page"
_COMPLETE_CONSTRAINT = "chk_field_source_location_complete"
_RANGE_CONSTRAINT = "chk_field_source_bbox_range"


def upgrade() -> None:
    op.add_column(_TABLE_NAME, sa.Column(_PAGE_COLUMN, sa.Integer(), nullable=True))
    for column_name in _BBOX_COLUMNS:
        op.add_column(_TABLE_NAME, sa.Column(column_name, sa.Numeric(10, 2), nullable=True))

    op.create_check_constraint(
        _PAGE_CONSTRAINT,
        _TABLE_NAME,
        f"{_PAGE_COLUMN} IS NULL OR {_PAGE_COLUMN} >= 1",
    )
    op.create_check_constraint(
        _COMPLETE_CONSTRAINT,
        _TABLE_NAME,
        "("
        "source_page IS NULL AND source_bbox_x IS NULL AND source_bbox_y IS NULL "
        "AND source_bbox_width IS NULL AND source_bbox_height IS NULL"
        ") OR ("
        "source_page IS NOT NULL AND source_bbox_x IS NOT NULL AND source_bbox_y IS NOT NULL "
        "AND source_bbox_width IS NOT NULL AND source_bbox_height IS NOT NULL"
        ")",
    )
    op.create_check_constraint(
        _RANGE_CONSTRAINT,
        _TABLE_NAME,
        "(source_bbox_x IS NULL OR source_bbox_x >= 0) "
        "AND (source_bbox_y IS NULL OR source_bbox_y >= 0) "
        "AND (source_bbox_width IS NULL OR source_bbox_width > 0) "
        "AND (source_bbox_height IS NULL OR source_bbox_height > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(_RANGE_CONSTRAINT, _TABLE_NAME, type_="check")
    op.drop_constraint(_COMPLETE_CONSTRAINT, _TABLE_NAME, type_="check")
    op.drop_constraint(_PAGE_CONSTRAINT, _TABLE_NAME, type_="check")
    for column_name in reversed(_BBOX_COLUMNS):
        op.drop_column(_TABLE_NAME, column_name)
    op.drop_column(_TABLE_NAME, _PAGE_COLUMN)
