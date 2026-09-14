"""Merge user consent and knowledge evidence migration heads.

Revision ID: 207c1d2e3f4a
Revises: 178a1b2c3d4e, 207b1c2d3e4
Create Date: 2026-09-14
"""

from collections.abc import Sequence

revision: str = "207c1d2e3f4a"
down_revision: str | Sequence[str] | None = ("178a1b2c3d4e", "207b1c2d3e4")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
