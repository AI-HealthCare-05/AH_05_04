"""Persist exact terminal retrieval replay payload (#180 B5).

Revision ID: 880a1b2c3d4e
Revises: 869a1b2c3d4e
"""

import sqlalchemy as sa
from alembic import op

revision = "880a1b2c3d4e"
down_revision = "869a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("retrieval_run", sa.Column("terminal_replay_payload", sa.JSON(), nullable=True))
    op.add_column("retrieval_run", sa.Column("terminal_replay_payload_hash", sa.String(64), nullable=True))
    op.create_check_constraint(
        "chk_retrieval_run_terminal_replay_payload_hash",
        "retrieval_run",
        "terminal_replay_payload_hash IS NULL OR terminal_replay_payload_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "chk_retrieval_run_terminal_replay_payload_pair",
        "retrieval_run",
        "(terminal_replay_payload IS NULL) = (terminal_replay_payload_hash IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("chk_retrieval_run_terminal_replay_payload_pair", "retrieval_run", type_="check")
    op.drop_constraint("chk_retrieval_run_terminal_replay_payload_hash", "retrieval_run", type_="check")
    op.drop_column("retrieval_run", "terminal_replay_payload_hash")
    op.drop_column("retrieval_run", "terminal_replay_payload")
