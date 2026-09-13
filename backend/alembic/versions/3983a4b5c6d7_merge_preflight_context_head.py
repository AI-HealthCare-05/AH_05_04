"""Merge #398 integrity and #412 preflight context migration histories.

Revision ID: 3983a4b5c6d7
Revises: 398293a4b5c6, 174a1b2c3d4e
Create Date: 2026-09-10
"""

from collections.abc import Sequence

revision: str = "3983a4b5c6d7"
down_revision: str | Sequence[str] | None = ("398293a4b5c6", "174a1b2c3d4e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
