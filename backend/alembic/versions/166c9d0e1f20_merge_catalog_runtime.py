"""Merge Catalog and Runtime histories without schema or data changes."""

from collections.abc import Sequence

revision: str = "166c9d0e1f20"
down_revision: str | Sequence[str] | None = ("166b8c9d0e1f", "175a1b2c3d4e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
