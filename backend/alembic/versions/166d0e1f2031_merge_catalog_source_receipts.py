"""Merge Catalog and Source Receipt histories without changing schema or data."""

from collections.abc import Sequence

revision: str = "166d0e1f2031"
down_revision: str | Sequence[str] | None = ("166c9d0e1f20", "362c3d4e5f60")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
