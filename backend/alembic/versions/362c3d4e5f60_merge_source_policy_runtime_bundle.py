"""Merge #362 Source policy and #416 Runtime Bundle migration histories.

Revision ID: 362c3d4e5f60
Revises: 362b2c3d4e5f, 175a1b2c3d4e

No data or schema operations. Preserve the already published migration revisions.
"""

from collections.abc import Sequence

revision: str = "362c3d4e5f60"
down_revision: str | Sequence[str] | None = ("362b2c3d4e5f", "175a1b2c3d4e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
