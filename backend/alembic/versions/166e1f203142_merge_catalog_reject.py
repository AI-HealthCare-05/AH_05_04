"""Merge Catalog and #444 histories, preserving both applied branches."""

from collections.abc import Sequence

revision: str = "166e1f203142"
down_revision: str | Sequence[str] | None = ("166d0e1f2031", "165a0b1c2d3e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
